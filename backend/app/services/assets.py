"""Licensed stock search and deterministic thumbnail variants."""

from __future__ import annotations

import json
import textwrap
import urllib.request
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont


class AssetService:
    def __init__(self, directory: str, pexels_key: str | None = None, pixabay_key: str | None = None, gameplay_dir: str | None = None, gameplay_manifest: str | None = None) -> None:
        self.directory = Path(directory)
        self.pexels_key = pexels_key
        self.pixabay_key = pixabay_key
        self.gameplay_dir = Path(gameplay_dir) if gameplay_dir else self.directory / "gameplay"
        self.gameplay_manifest = Path(gameplay_manifest) if gameplay_manifest else self.gameplay_dir / "license-manifest.json"

    def licensed_gameplay(self, *, limit: int = 1) -> list[dict[str, Any]]:
        """Return only owner-declared gameplay assets with an explicit license."""
        if not self.gameplay_manifest.exists():
            return []
        try:
            manifest = json.loads(self.gameplay_manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        entries = manifest.get("assets", []) if isinstance(manifest, dict) else []
        allowed: list[dict[str, Any]] = []
        root = self.gameplay_dir.resolve()
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("allowed") is not True or not entry.get("license") or not entry.get("source"):
                continue
            candidate = (root / str(entry.get("path", ""))).resolve()
            if not candidate.is_relative_to(root) or not candidate.is_file() or candidate.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm"}:
                continue
            allowed.append({"type": "licensed_gameplay", "local_path": str(candidate), "license": str(entry["license"]), "source": str(entry["source"]), "attribution": str(entry.get("attribution", "")), "allowed": True})
            if len(allowed) >= limit:
                break
        return allowed

    def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if self.pexels_key:
            response = httpx.get(
                "https://api.pexels.com/v1/search",
                params={"query": query, "per_page": min(limit, 80)},
                headers={"Authorization": self.pexels_key},
                timeout=20,
            )
            response.raise_for_status()
            results.extend(
                {"provider": "pexels", "url": item["src"]["large2x"], "page": item["url"], "license": "Pexels license", "alt": item.get("alt", query)}
                for item in response.json().get("photos", [])
            )
        if self.pixabay_key and len(results) < limit:
            response = httpx.get(
                "https://pixabay.com/api/",
                params={"key": self.pixabay_key, "q": query, "image_type": "photo", "per_page": min(limit - len(results), 20)},
                timeout=20,
            )
            response.raise_for_status()
            results.extend(
                {"provider": "pixabay", "url": item["largeImageURL"], "page": item["pageURL"], "license": "Pixabay Content License", "alt": query}
                for item in response.json().get("hits", [])
            )
        return results[:limit]

    def download(self, asset: dict[str, Any], output: Path) -> dict[str, Any]:
        output.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(asset["url"], headers={"User-Agent": "OrbitChannelBot/1.0"})
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - URL came from configured stock API
            output.write_bytes(response.read())
        return {**asset, "local_path": str(output)}


class ThumbnailService:
    def __init__(self, directory: str) -> None:
        self.directory = Path(directory)
        self.font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    def _font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        try:
            return ImageFont.truetype(self.font_path, size)
        except OSError:
            return ImageFont.load_default()

    def create_variants(self, *, video_id: str, title: str, topic: str) -> list[str]:
        folder = self.directory / video_id
        folder.mkdir(parents=True, exist_ok=True)
        palette = [(19, 52, 39), (40, 73, 130), (130, 62, 35)]
        variants: list[str] = []
        short_title = " ".join(title.replace("—", " ").split()[:5]).upper()
        for index, colour in enumerate(palette, start=1):
            image = Image.new("RGB", (1280, 720), colour)
            draw = ImageDraw.Draw(image)
            draw.ellipse((860, -110, 1430, 470), fill=(255, 255, 255, 26))
            draw.rounded_rectangle((60, 55, 1220, 665), radius=24, outline=(255, 255, 255), width=4)
            draw.text((95, 100), "ORBIT GAMING", font=self._font(30), fill=(190, 240, 204))
            lines = textwrap.wrap(short_title or topic.upper(), width=18)[:3]
            y = 235
            for line in lines:
                draw.text((95, y), line, font=self._font(76), fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0))
                y += 92
            draw.text((95, 600), "MYTH YA FACT?  •  HINGLISH GAMING", font=self._font(25), fill=(235, 220, 175))
            path = folder / f"thumbnail-{index}.jpg"
            image.save(path, quality=92, optimize=True)
            variants.append(str(path))
        return variants
