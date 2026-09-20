"""YouTube OAuth, channel sync, analytics and resumable upload adapter."""

from __future__ import annotations

import json
import os
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import Settings
from ..db import Database, json_dumps, utc_now

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


class YouTubeNotConfigured(RuntimeError):
    """Raised when OAuth or the Google client file has not been set up."""


class YouTubeService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def _imports(self):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import Flow
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
        except ImportError as exc:  # pragma: no cover - dependency is installed in production extra
            raise YouTubeNotConfigured(
                "Install the provider dependencies with: pip install -e '.[providers]'"
            ) from exc
        return Request, Credentials, Flow, build, MediaFileUpload

    def _save_token(self, credentials: Any) -> None:
        path = Path(self.settings.youtube_token_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(credentials.to_json(), encoding="utf-8")
        os.chmod(path, 0o600)

    def _credentials(self) -> Any:
        Request, Credentials, _, _, _ = self._imports()
        token_path = Path(self.settings.youtube_token_file)
        if not token_path.exists():
            raise YouTubeNotConfigured(f"YouTube OAuth token not found at {token_path}")
        credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            self._save_token(credentials)
        if not credentials.valid:
            raise YouTubeNotConfigured("YouTube OAuth token is invalid; authorize the channel again")
        return credentials

    def status(self) -> dict[str, Any]:
        client_file = Path(self.settings.youtube_client_secret_file)
        token_file = Path(self.settings.youtube_token_file)
        if not client_file.exists():
            value = {
                "connected": False,
                "status": "not_configured",
                "message": f"Add the OAuth client JSON at {client_file}",
            }
        elif not token_file.exists():
            value = {
                "connected": False,
                "status": "needs_authorization",
                "message": "OAuth client is present; authorize the channel once",
            }
        else:
            try:
                self._credentials()
                value = {"connected": True, "status": "healthy", "message": "YouTube channel connected"}
            except Exception as exc:  # noqa: BLE001 - status must not take down the dashboard
                value = {"connected": False, "status": "error", "message": str(exc)}
        self.database.update_provider_health(
            "youtube", status=value["status"], role="youtube", error=None if value["connected"] else value["message"]
        )
        return value

    def authorization_url(self) -> str:
        _, _, Flow, _, _ = self._imports()
        client_file = Path(self.settings.youtube_client_secret_file)
        if not client_file.exists():
            raise YouTubeNotConfigured(f"OAuth client JSON not found at {client_file}")
        flow = Flow.from_client_secrets_file(str(client_file), scopes=SCOPES)
        flow.redirect_uri = self.settings.youtube_redirect_uri
        url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        self.database.set_setting("youtube_oauth_state", state)
        return url

    def complete_authorization(self, code: str, state: str | None) -> dict[str, Any]:
        _, _, Flow, _, _ = self._imports()
        expected = self.database.setting("youtube_oauth_state")
        if not expected or not state or not secrets.compare_digest(expected, state):
            raise YouTubeNotConfigured("OAuth state did not match; restart authorization")
        flow = Flow.from_client_secrets_file(
            self.settings.youtube_client_secret_file,
            scopes=SCOPES,
            state=state,
        )
        flow.redirect_uri = self.settings.youtube_redirect_uri
        flow.fetch_token(code=code)
        self._save_token(flow.credentials)
        self.database.set_setting("youtube_oauth_state", "")
        channel = self.get_channel()
        if self.database.setting("youtube_connected_once", "false") != "true":
            self.database.clear_preview_records()
            self.database.set_setting("youtube_connected_once", "true")
        return channel

    def _api(self) -> Any:
        _, _, _, build, _ = self._imports()
        return build("youtube", "v3", credentials=self._credentials(), cache_discovery=False)

    def _analytics(self) -> Any:
        _, _, _, build, _ = self._imports()
        return build("youtubeAnalytics", "v2", credentials=self._credentials(), cache_discovery=False)

    def get_channel(self) -> dict[str, Any]:
        response = self._api().channels().list(part="snippet,statistics,contentDetails,status", mine=True).execute()
        items = response.get("items", [])
        if not items:
            raise YouTubeNotConfigured("The authorized Google account has no accessible YouTube channel")
        channel = items[0]
        self.database.set_setting("youtube_channel", json_dumps(channel))
        self.settings.youtube_channel_id = channel["id"]
        return channel

    def _uploads_playlist(self, channel: dict[str, Any]) -> str:
        return channel["contentDetails"]["relatedPlaylists"]["uploads"]

    def list_uploads(self, *, max_results: int = 50) -> list[dict[str, Any]]:
        channel = self.get_channel()
        playlist_id = self._uploads_playlist(channel)
        playlist = self._api().playlistItems().list(
            part="snippet,contentDetails", playlistId=playlist_id, maxResults=min(max_results, 50)
        ).execute()
        ids = [item["contentDetails"]["videoId"] for item in playlist.get("items", [])]
        if not ids:
            return []
        response = self._api().videos().list(
            part="snippet,statistics,status,contentDetails", id=",".join(ids)
        ).execute()
        return response.get("items", [])

    def sync_channel(self, *, max_results: int = 50) -> dict[str, Any]:
        channel = self.get_channel()
        videos = self.list_uploads(max_results=max_results)
        imported = 0
        for item in videos:
            snippet = item.get("snippet", {})
            status = item.get("status", {})
            video = self.database.get_video_by_youtube_id(item["id"])
            payload = {
                "id": video["id"] if video else None,
                "youtube_video_id": item["id"],
                "youtube_url": f"https://www.youtube.com/watch?v={item['id']}",
                "status": "published" if status.get("privacyStatus") == "public" else status.get("privacyStatus", "private"),
                "format": "short" if (item.get("contentDetails", {}).get("duration", "").startswith("PT") and "M" not in item.get("contentDetails", {}).get("duration", "")) else "long",
                "content_type": "imported",
                "topic": snippet.get("title", "Imported YouTube video"),
                "title": snippet.get("title"),
                "script": None,
                "dry_run": False,
            }
            if video:
                local_video_id = video["id"]
                self.database.update_video(video["id"], **{key: value for key, value in payload.items() if key not in {"id", "format", "content_type", "topic", "dry_run"}})
            else:
                local_video_id = self.database.create_video(payload)["id"]
            stats = item.get("statistics", {})
            self.database.add_metric(
                local_video_id,
                {
                    "views": int(stats.get("viewCount", 0)),
                    "likes": int(stats.get("likeCount", 0)),
                    "comments": int(stats.get("commentCount", 0)),
                    "source": "youtube-data-api",
                },
            )
            imported += 1
        return {"channel": channel, "videos_synced": imported, "synced_at": utc_now()}

    def set_thumbnail(self, video_id: str, thumbnail_path: str) -> dict[str, Any]:
        _, _, _, _, MediaFileUpload = self._imports()
        return self._api().thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumbnail_path, mimetype="image/jpeg"),
        ).execute()

    def list_comments(self, video_id: str, *, max_results: int = 100) -> list[dict[str, Any]]:
        response = self._api().commentThreads().list(
            part="snippet,replies", videoId=video_id, maxResults=min(max_results, 100), textFormat="plainText"
        ).execute()
        return [
            {
                "id": item["id"],
                **item.get("snippet", {}).get("topLevelComment", {}),
            }
            for item in response.get("items", [])
        ]

    def reply_to_comment(self, parent_id: str, text: str) -> dict[str, Any]:
        return self._api().comments().insert(
            part="snippet",
            body={"snippet": {"parentId": parent_id, "textOriginal": text[:10000]}},
        ).execute()

    def analytics_snapshot(self, *, days: int = 28) -> dict[str, Any]:
        end = datetime.now(UTC).date()
        start = end - timedelta(days=days)
        return self._analytics().reports().query(
            ids="channel==MINE",
            startDate=start.isoformat(),
            endDate=end.isoformat(),
            metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,comments,subscribersGained,subscribersLost",
            dimensions="day",
            sort="day",
        ).execute()

    def upload_video(
        self,
        *,
        media_path: str,
        title: str,
        description: str,
        tags: list[str],
        publish_at: str | None,
        category_id: str = "20",
    ) -> dict[str, Any]:
        _, _, _, build, MediaFileUpload = self._imports()
        api = self._api()
        body: dict[str, Any] = {
            "snippet": {"title": title[:100], "description": description[:5000], "tags": tags[:15], "categoryId": category_id, "defaultLanguage": "hi"},
            "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": False},
        }
        if publish_at:
            body["status"]["publishAt"] = publish_at
        media = MediaFileUpload(media_path, chunksize=8 * 1024 * 1024, resumable=True)
        request = api.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            _, response = request.next_chunk()
        video_id = response["id"]
        # Upload completion is not processing completion. Poll before marking
        # the job successful so a retry cannot hide a rejected media file.
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            processed = api.videos().list(part="status,processingDetails", id=video_id).execute()
            item = (processed.get("items") or [{}])[0]
            processing = item.get("processingDetails", {}).get("processingStatus")
            if processing in {None, "succeeded"}:
                break
            if processing in {"failed", "terminated"}:
                raise RuntimeError(f"YouTube processing failed for uploaded video {video_id}")
            time.sleep(5)
        else:
            raise RuntimeError(f"YouTube processing timed out for uploaded video {video_id}")
        return {"id": video_id, "url": f"https://www.youtube.com/watch?v={video_id}", "published_at": publish_at}
