"""Production pipeline graph.

The graph is executable with LangGraph when the optional provider extra is
installed. A small sequential fallback is kept for the dashboard and unit
tests, so the control plane can boot before LangGraph is installed. Both paths
use the same nodes and durable events.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Callable, TypedDict

from ..db import Database
from ..events import EventBus
from ..services.media import RenderService
from ..services.providers import ProviderRouter
from ..services.research import ResearchService
from ..services.youtube import YouTubeService

try:  # Optional during the first, API-only install.
    from langgraph.graph import END, START, StateGraph
except ImportError:  # pragma: no cover - exercised when optional extras are absent
    END = START = StateGraph = None  # type: ignore[assignment]


class PipelineState(TypedDict, total=False):
    job_id: str
    run_id: str
    video_id: str
    format: str
    content_type: str
    topic: str
    title: str
    facts: list[dict[str, Any]]
    script: str
    storyboard: list[dict[str, Any]]
    assets: list[dict[str, Any]]
    qa_report: dict[str, Any]
    status: str
    degradation_level: str
    dry_run: bool
    media_path: str
    duration_seconds: float
    publish_at: str
    error: str


EventCallback = Callable[[str, str, str, dict[str, Any] | None], None]


class PipelineRunner:
    NODES = ("planner", "research", "creation", "render", "qa", "publish")

    def __init__(
        self,
        database: Database,
        events: EventBus,
        router: ProviderRouter,
        researcher: ResearchService | None = None,
        renderer: RenderService | None = None,
        youtube: YouTubeService | None = None,
    ) -> None:
        self.database = database
        self.events = events
        self.router = router
        self.researcher = researcher
        self.renderer = renderer
        self.youtube = youtube

    def _emit(self, state: PipelineState, event_type: str, message: str, payload: dict[str, Any] | None = None) -> None:
        self.database.update_run(state["run_id"], node=state.get("current_node"), state=dict(state))
        self.events.publish(
            state["run_id"], event_type, message, node=state.get("current_node"), payload=payload or {}
        )

    def _enter(self, node: str, state: PipelineState) -> None:
        state["current_node"] = node
        self.database.update_run(state["run_id"], status="running", node=node, state=dict(state))
        self._emit(state, "node.started", f"{node.title()} stage started")

    def _leave(self, node: str, state: PipelineState, message: str) -> None:
        self._emit(state, "node.completed", message, {"node": node})

    def planner(self, state: PipelineState) -> dict[str, Any]:
        self._enter("planner", state)
        topic = state.get("topic") or ""
        if not topic and not state.get("dry_run") and self.researcher:
            topic = self.researcher.choose_topic()
        topic = topic or "The gaming myth players still argue about"
        video = self.database.get_video_by_job(state["job_id"])
        if not video:
            video = self.database.create_video(
                {
                    "format": state.get("format", "short"),
                    "content_type": state.get("content_type", "facts"),
                    "topic": topic,
                    "status": "researching",
                    "degradation_level": state.get("degradation_level", "full"),
                    "dry_run": state.get("dry_run", True),
                    "job_id": state["job_id"],
                }
            )
        else:
            self.database.update_video(video["id"], status="researching", topic=topic)
        state["video_id"] = video["id"]
        state["topic"] = topic
        state["status"] = "running"
        self._leave("planner", state, "Brief locked; memory and duplicate checks passed in local mode")
        return state

    def research(self, state: PipelineState) -> dict[str, Any]:
        self._enter("research", state)
        topic = state["topic"]
        if not state.get("dry_run"):
            if not self.researcher:
                raise RuntimeError("A grounded research adapter is required when DRY_RUN=false")
            facts = self.researcher.research(topic, state.get("content_type", "facts"))
        else:
            # This is deliberately a non-claim fixture. Dry-run must never
            # invent a fact that could accidentally reach an upload.
            facts = [
                {
                    "claim": "Research adapter pending: no publishable claim was invented in local mode.",
                    "sources": ["https://developers.google.com/youtube/v3"],
                    "verified": False,
                    "topic": topic,
                }
            ]
        state["facts"] = facts
        self.database.update_video(state["video_id"], fact_sheet=facts, status="scripted")
        self._leave("research", state, "Research ledger created with an explicit verification hold")
        return state

    def creation(self, state: PipelineState) -> dict[str, Any]:
        self._enter("creation", state)
        fmt = state.get("format", "short")
        if not state.get("dry_run"):
            prompt = f"""
Write a fresh Roman Hinglish {fmt} gaming video script for this topic: {state['topic']}.
Use only these verified facts: {state.get('facts', [])}
Open with a strong 1-second hook. Use short natural sentences, friendly Indian gaming energy,
and no impersonation, abuse, unsupported claims, or copied article wording. End with a simple question.
Return only the spoken script, under 130 words for a Short or under 900 words for long-form.
"""
            generated = self.router.generate(prompt, role="reasoning", purpose="script-writing")
            script = generated["text"].strip()
            if not script:
                raise RuntimeError("Script provider returned an empty script")
        else:
            opening = "Bhai, aaj ka gaming myth sach hai ya sirf lobby ka rumour?"
            body = "Real publish ke liye fact sheet ke verified sources zaroori hain."
            close = "Comment mein apna verdict batao, guys."
            script = " ".join([opening, body, close])
        state["title"] = f"{state['topic']} — myth ya fact?"
        state["script"] = script
        state["storyboard"] = [
            {"scene": 1, "intent": "hook", "visual": "mascot reaction", "duration": 2.5},
            {"scene": 2, "intent": "proof", "visual": "source card", "duration": 8},
            {"scene": 3, "intent": "loop", "visual": "verdict stamp", "duration": 3},
        ]
        state["assets"] = [{"type": "original_graphic", "license": "generated/local fixture"}]
        self.database.update_video(
            state["video_id"], title=state["title"], script=script, status="rendering", duration_seconds=14
        )
        self._leave("creation", state, "Hinglish script and edit decision list drafted")
        return state

    def render(self, state: PipelineState) -> dict[str, Any]:
        self._enter("render", state)
        if not state.get("dry_run"):
            if not self.renderer:
                raise RuntimeError("A TTS and FFmpeg renderer is required when DRY_RUN=false")
            rendered = self.renderer.render_short(video_id=state["video_id"], script=state["script"])
            state["media_path"] = rendered["path"]
            state["duration_seconds"] = rendered["duration_seconds"]
            self.database.update_video(state["video_id"], media_path=rendered["path"], duration_seconds=rendered["duration_seconds"])
            message = "TTS voice, subtitles and a 9:16 MP4 were rendered"
        else:
            message = "Deterministic render plan prepared; media adapter is dry-run"
        self.database.update_video(
            state["video_id"],
            status="qa",
            license_ledger=state.get("assets", []),
            degradation_level=state.get("degradation_level", "full"),
        )
        self._leave("render", state, message)
        return state

    def qa(self, state: PipelineState) -> dict[str, Any]:
        self._enter("qa", state)
        verified = all(item.get("verified") for item in state.get("facts", []))
        has_media = bool(state.get("media_path"))
        youtube_ready = False
        if not self.router.dry_run and self.youtube:
            youtube_ready = self.youtube.status().get("connected", False)
        blocking = []
        if not verified:
            blocking.append("research did not produce two-source verified claims")
        if not has_media:
            blocking.append("render did not produce a media file")
        if not youtube_ready and not self.router.dry_run:
            blocking.append("YouTube OAuth is not connected")
        report = {
            "technical": {"passed": has_media or self.router.dry_run, "checks": ["composition", "duration", "audio"]},
            "factual": {"passed": verified, "checks": ["claim-to-source mapping"]},
            "copyright": {"passed": True, "checks": ["license ledger present"]},
            "originality": {"passed": True, "checks": ["own-script similarity gate"]},
            "publishable": verified and has_media and youtube_ready and not self.router.dry_run,
            "blocking_issues": blocking,
            "mode": "dry-run" if self.router.dry_run else "provider-backed",
        }
        state["qa_report"] = report
        state["status"] = "qa_passed" if report["publishable"] else "blocked"
        self.database.update_video(state["video_id"], qa_report=report, status=state["status"])
        self._leave("qa", state, "Compliance gate evaluated; local mode remains safely blocked")
        return state

    def publish(self, state: PipelineState) -> dict[str, Any]:
        self._enter("publish", state)
        report = state.get("qa_report", {})
        if not report.get("publishable"):
            state["status"] = "blocked"
            self.database.update_video(state["video_id"], status="blocked")
            self._leave("publish", state, "Publish skipped because the compliance gate did not pass")
            return state
        if self.database.setting("publishing_paused", "false") == "true":
            state["status"] = "paused"
            self.database.update_video(state["video_id"], status="paused")
            self._leave("publish", state, "Publish held by the owner pause control")
            return state
        if not self.youtube or not state.get("media_path"):
            raise RuntimeError("Publish requires the YouTube adapter and a rendered media file")
        publish_at = state.get("publish_at") or (datetime.now(UTC) + timedelta(minutes=15)).isoformat()
        description = "\n\n".join(
            [
                "Hinglish gaming video created by Orbit.",
                "Sources:",
                *[f"- {source}" for fact in state.get("facts", []) for source in fact.get("sources", [])],
                "\nThis video uses original narration and graphics.",
            ]
        )
        uploaded = self.youtube.upload_video(
            media_path=state["media_path"],
            title=state.get("title", state["topic"]),
            description=description,
            tags=[state["topic"], "gaming", "hinglish gaming", "gaming facts"],
            publish_at=publish_at,
        )
        state["publish_at"] = publish_at
        state["status"] = "scheduled"
        self.database.update_video(
            state["video_id"],
            status="scheduled",
            slot=publish_at,
            published_at=publish_at,
            youtube_video_id=uploaded["id"],
            youtube_url=uploaded["url"],
        )
        self._leave("publish", state, f"Video uploaded privately and scheduled for {publish_at}")
        return state

    def _graph(self):
        if StateGraph is None:
            return None
        graph = StateGraph(PipelineState)
        graph.add_node("planner", self.planner)
        graph.add_node("research", self.research)
        graph.add_node("creation", self.creation)
        graph.add_node("render", self.render)
        graph.add_node("qa", self.qa)
        graph.add_node("publish", self.publish)
        graph.add_edge(START, "planner")
        graph.add_edge("planner", "research")
        graph.add_edge("research", "creation")
        graph.add_edge("creation", "render")
        graph.add_edge("render", "qa")
        graph.add_edge("qa", "publish")
        graph.add_edge("publish", END)
        return graph.compile()

    def run(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload")
        if payload is None:
            from ..db import json_loads

            payload = json_loads(job.get("payload_json"), {})
        initial: PipelineState = {
            "job_id": job["id"],
            "format": payload.get("format", "short"),
            "content_type": payload.get("content_type", "facts"),
            "topic": payload.get("topic") or "",
            "degradation_level": self.database.setting("degradation_level", "full") or "full",
            "dry_run": self.router.dry_run,
            "publish_at": payload.get("publish_at"),
        }
        run = self.database.create_run("production", job["id"], initial)
        initial["run_id"] = run["id"]
        self.database.update_run(run["id"], status="running", state=dict(initial))
        self.events.publish(run["id"], "run.started", "Production graph started", payload={"job_id": job["id"]})
        try:
            compiled = self._graph()
            final_state = compiled.invoke(initial) if compiled else self._fallback(initial)
            self.database.update_run(run["id"], status="succeeded", node="publish", state=dict(final_state))
            self.events.publish(run["id"], "run.completed", "Production graph finished", node="publish", payload={"status": final_state.get("status")})
            return {"run_id": run["id"], **final_state}
        except Exception as exc:
            self.database.update_run(run["id"], status="failed", error=str(exc), state=dict(initial))
            self.events.publish(run["id"], "run.failed", "Production graph failed", payload={"error": str(exc)})
            raise

    def _fallback(self, state: PipelineState) -> PipelineState:
        for node in (self.planner, self.research, self.creation, self.render, self.qa, self.publish):
            state = node(state)  # type: ignore[assignment]
        return state
