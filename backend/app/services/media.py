"""Free, deterministic production editing with TTS and FFmpeg.

The editor deliberately does not download or remix game footage. It creates an
original motion-graphics package from generated scene cards, voice, captions,
and an original synthesized music bed. An owner-supplied/licensed still can be
used as a visual background, and its license stays in the video ledger.

FFmpeg is the only heavyweight editing dependency. This keeps the path free,
repeatable, and deployable in the included Docker image.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .alignment import AlignmentService


class MediaNotConfigured(RuntimeError):
    pass


class TTSService:
    def __init__(self, voice: str = "hi-IN-SwaraNeural") -> None:
        self.voice = voice

    async def _synthesize(self, text: str, output: Path) -> None:
        try:
            import edge_tts
        except ImportError as exc:  # pragma: no cover
            raise MediaNotConfigured("Install edge-tts to enable the Hinglish voice") from exc
        communicate = edge_tts.Communicate(text, self.voice)
        await communicate.save(str(output))

    def synthesize(self, text: str, output: Path) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            asyncio.run(self._synthesize(text, output))
        except RuntimeError as exc:
            # A worker is synchronous today; keep the error actionable if a
            # future async worker accidentally calls this from an event loop.
            raise MediaNotConfigured(f"TTS synthesis could not start: {exc}") from exc
        return output


class RenderService:
    """Render polished vertical Shorts and horizontal long-form videos.

    The edit is intentionally explainable: scene cards, intro/outro timing,
    caption timing, voice/music mix, transitions, and technical dimensions are
    persisted in ``edit_manifest.json`` next to the MP4.
    """

    SHORT_SIZE = (1080, 1920)
    LONG_SIZE = (1920, 1080)
    FPS = 30

    def __init__(self, media_dir: str, voice: str = "hi-IN-SwaraNeural", groq_api_key: str | None = None) -> None:
        self.media_dir = Path(media_dir)
        self.tts = TTSService(voice)
        self.alignment = AlignmentService(groq_api_key)
        self.voice = voice

    @staticmethod
    def _duration(text: str, format_name: str) -> float:
        words = max(1, len(text.split()))
        estimated = words / 2.25
        if format_name == "short":
            return round(max(8.0, min(58.0, estimated)), 2)
        # Long-form is intentionally kept inside the channel contract. A short
        # script is padded with an original visual bed rather than published as
        # a mislabelled Short.
        return round(max(60.0, min(600.0, estimated)), 2)

    @staticmethod
    def _stamp(seconds: float) -> str:
        whole = int(max(0, seconds))
        return f"{whole // 3600:02d}:{(whole % 3600) // 60:02d}:{whole % 60:02d},{int((seconds % 1) * 1000):03d}"

    @staticmethod
    def _filter_path(path: Path) -> str:
        return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")

    def _write_subtitles(self, path: Path, aligned: list[dict[str, Any]], duration: float) -> None:
        groups = [aligned[index : index + 5] for index in range(0, len(aligned), 5)]
        path.write_text(
            "\n\n".join(
                f"{index + 1}\n{self._stamp(float(group[0]['start']))} --> {self._stamp(min(duration, float(group[-1]['end'])))}\n{' '.join(item['word'] for item in group)}"
                for index, group in enumerate(groups)
                if group
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        for candidate in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ):
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def _scene_card(
        self,
        path: Path,
        *,
        title: str,
        topic: str,
        intent: str,
        index: int,
        total: int,
        format_name: str,
        background_path: str | None,
    ) -> None:
        width, height = self.SHORT_SIZE if format_name == "short" else self.LONG_SIZE
        if background_path and Path(background_path).exists():
            try:
                source = Image.open(background_path).convert("RGB")
                source.thumbnail((width, height))
                image = Image.new("RGB", (width, height), (12, 22, 28))
                image.paste(source, ((width - source.width) // 2, (height - source.height) // 2))
            except OSError:
                image = Image.new("RGB", (width, height), (12, 22, 28))
        else:
            # Original vector-style cards are safe fallback visuals for gaming
            # explainers. They are not scraped gameplay and require no license.
            palette = [(14, 38, 45), (26, 45, 82), (59, 32, 68), (36, 64, 45)]
            image = Image.new("RGB", (width, height), palette[index % len(palette)])
        draw = ImageDraw.Draw(image, "RGBA")
        tint = [(76, 220, 150), (255, 185, 82), (119, 187, 255), (235, 117, 170)][index % 4]
        for radius, alpha in ((0.40, 75), (0.27, 110), (0.15, 135)):
            box = (int(width * (1 - radius)), int(height * -0.05), int(width * 1.08), int(height * radius))
            draw.ellipse(box, fill=(*tint, alpha))
        grid_gap = max(45, width // 24)
        for x in range(0, width, grid_gap):
            draw.line((x, 0, x, height), fill=(255, 255, 255, 14), width=1)
        for y in range(0, height, grid_gap):
            draw.line((0, y, width, y), fill=(255, 255, 255, 12), width=1)
        inset = int(width * 0.055)
        draw.rounded_rectangle((inset, inset, width - inset, height - inset), radius=max(18, width // 45), outline=(255, 255, 255, 135), width=max(2, width // 500))
        badge_font = self._font(max(24, width // 42))
        title_font = self._font(max(42, width // (11 if format_name == "short" else 23)))
        small_font = self._font(max(18, width // 58))
        draw.text((inset * 2, inset * 1.6), "ORBIT GAMING  /  ORIGINAL EDIT", font=badge_font, fill=(190, 242, 205, 255))
        draw.text((inset * 2, inset * 3.1), f"SCENE {index + 1:02d}  ·  {intent.upper()}", font=small_font, fill=(255, 203, 112, 255))
        lines = textwrap.wrap((title or topic or "Gaming myth ya fact?").upper(), width=17 if format_name == "short" else 29)[:4]
        y = int(height * (0.35 if format_name == "short" else 0.32))
        for line in lines:
            draw.text((inset * 2, y), line, font=title_font, fill=(255, 255, 255, 255), stroke_width=max(1, width // 700), stroke_fill=(0, 0, 0, 180))
            y += int(getattr(title_font, "size", max(42, width // (11 if format_name == "short" else 23)))) + max(10, width // 90)
        draw.rounded_rectangle((inset * 2, height - inset * 3, width - inset * 2, height - inset * 1.5), radius=12, fill=(5, 15, 19, 165))
        draw.text((inset * 2.5, height - inset * 2.55), f"{topic[:70]}   •   {index + 1}/{total}", font=small_font, fill=(230, 240, 232, 245))
        image.save(path, quality=94, optimize=True)

    def _make_scene_cards(
        self,
        folder: Path,
        *,
        title: str,
        topic: str,
        format_name: str,
        storyboard: list[dict[str, Any]] | None,
        background_path: str | None,
    ) -> list[dict[str, Any]]:
        default_intents = ["hook", "context", "proof", "payoff", "question"]
        raw = storyboard or [{"intent": intent} for intent in default_intents]
        # Keep a human-readable edit decision list while preventing a bad
        # provider response from producing hundreds of FFmpeg inputs.
        scenes = raw[:12] if format_name == "long" else raw[:6]
        total = len(scenes) or 1
        result: list[dict[str, Any]] = []
        for index, item in enumerate(scenes):
            path = folder / f"scene-{index + 1:02d}.jpg"
            self._scene_card(
                path,
                title=title,
                topic=topic,
                intent=str(item.get("intent") or item.get("visual") or default_intents[index % len(default_intents)]),
                index=index,
                total=total,
                format_name=format_name,
                background_path=background_path,
            )
            result.append(
                {
                    "index": index + 1,
                    "intent": str(item.get("intent") or "beat"),
                    "visual": str(item.get("visual") or "original motion card"),
                    "duration_seconds": float(item.get("duration") or (3.5 if format_name == "short" else 12.0)),
                    "path": str(path),
                }
            )
        # Scale scene durations to the final format duration. This guarantees
        # the intro, body, and outro all fit a strict YouTube contract.
        duration = self._duration(" ".join(str(item.get("intent", "")) for item in scenes), format_name)
        total_scene_time = sum(item["duration_seconds"] for item in result) or 1
        for item in result:
            item["duration_seconds"] = round(duration * item["duration_seconds"] / total_scene_time, 3)
        return result

    def _run(self, command: list[str], timeout: int = 600) -> None:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            raise MediaNotConfigured(f"FFmpeg command failed: {result.stderr[-1200:]}")

    def _scene_video(self, ffmpeg: str, scene: dict[str, Any], output: Path, format_name: str) -> None:
        width, height = self.SHORT_SIZE if format_name == "short" else self.LONG_SIZE
        duration = float(scene["duration_seconds"])
        fade_out = max(0.4, duration - 0.35)
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
            f"fps={self.FPS},format=yuv420p,fade=t=in:st=0:d=0.28,fade=t=out:st={fade_out:.3f}:d=0.35"
        )
        self._run([
            ffmpeg, "-y", "-loop", "1", "-i", scene["path"], "-t", f"{duration:.3f}",
            "-vf", vf, "-r", str(self.FPS), "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
        ], timeout=180)

    def _gameplay_visual(self, ffmpeg: str, source: str, output: Path, duration: float, format_name: str) -> None:
        width, height = self.SHORT_SIZE if format_name == "short" else self.LONG_SIZE
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
            "eq=contrast=1.05:saturation=1.08:brightness=0.01,format=yuv420p"
        )
        self._run([
            ffmpeg, "-y", "-stream_loop", "-1", "-i", source, "-t", f"{duration:.3f}",
            "-vf", vf, "-r", str(self.FPS), "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
        ], timeout=600)

    def _music_bed(self, ffmpeg: str, output: Path, duration: float) -> None:
        # Two low-volume sine layers make an original ambient pulse. No music
        # library, account, attribution or copyright claim is involved.
        command = [
            ffmpeg, "-y", "-f", "lavfi", "-i", f"sine=frequency=196:sample_rate=48000:duration={duration:.3f}",
            "-f", "lavfi", "-i", f"sine=frequency=293.66:sample_rate=48000:duration={duration:.3f}",
            "-filter_complex", "[0:a]volume=0.045[a0];[1:a]volume=0.025[a1];[a0][a1]amix=inputs=2:duration=longest,afade=t=in:st=0:d=1,afade=t=out:st=" + f"{max(1, duration - 1):.3f}:d=1[a]",
            "-map", "[a]", "-c:a", "pcm_s16le", str(output),
        ]
        self._run(command, timeout=180)

    def _concat_scenes(self, ffmpeg: str, scene_paths: list[Path], output: Path) -> None:
        manifest = output.with_suffix(".txt")
        manifest.write_text("\n".join(f"file '{path.resolve()}'" for path in scene_paths), encoding="utf-8")
        self._run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(manifest), "-c", "copy", str(output)], timeout=300)

    def _compose(
        self,
        ffmpeg: str,
        *,
        visual: Path,
        voice: Path,
        music: Path,
        subtitles: Path,
        output: Path,
        duration: float,
        format_name: str,
    ) -> None:
        width, height = self.SHORT_SIZE if format_name == "short" else self.LONG_SIZE
        margin = 260 if format_name == "short" else 78
        subtitle_style = (
            f"FontName=DejaVu Sans,FontSize={'22' if format_name == 'short' else '18'},"
            f"PrimaryColour=&H00FFFFFF,OutlineColour=&H0010231B,BorderStyle=3,Outline=2,MarginV={margin}"
        )
        subtitle_filter = f"subtitles='{self._filter_path(subtitles)}':force_style='{subtitle_style}'"
        filter_graph = (
            f"[0:v]scale={width}:{height},setsar=1,{subtitle_filter}[v];"
            f"[1:a]apad=pad_dur={duration:.3f},atrim=duration={duration:.3f},volume=1.0[voice];"
            f"[2:a]volume=0.10,atrim=duration={duration:.3f}[bed];"
            "[voice][bed]amix=inputs=2:duration=first:dropout_transition=2,alimiter=limit=0.95[a]"
        )
        self._run([
            ffmpeg, "-y", "-i", str(visual), "-i", str(voice), "-i", str(music),
            "-filter_complex", filter_graph, "-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}",
            "-r", str(self.FPS), "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(output),
        ], timeout=600)

    def inspect(self, media_path: str | Path) -> dict[str, Any]:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            raise MediaNotConfigured("FFprobe is not installed; install FFmpeg to inspect rendered media")
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(media_path)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode:
            raise MediaNotConfigured(f"FFprobe failed: {result.stderr[-800:]}")
        data = json.loads(result.stdout)
        video = next((stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"), {})
        audio = next((stream for stream in data.get("streams", []) if stream.get("codec_type") == "audio"), {})
        return {
            "width": int(video.get("width", 0)),
            "height": int(video.get("height", 0)),
            "duration_seconds": float(data.get("format", {}).get("duration") or video.get("duration") or 0),
            "has_audio": bool(audio),
            "video_codec": video.get("codec_name"),
            "audio_codec": audio.get("codec_name"),
        }

    def render(
        self,
        *,
        video_id: str,
        script: str,
        title: str,
        topic: str,
        format_name: str = "short",
        storyboard: list[dict[str, Any]] | None = None,
        background_path: str | None = None,
    ) -> dict[str, Any]:
        if format_name not in {"short", "long"}:
            raise MediaNotConfigured(f"Unsupported format: {format_name}")
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise MediaNotConfigured("FFmpeg is not installed; install it with apt install ffmpeg")
        folder = self.media_dir / video_id
        folder.mkdir(parents=True, exist_ok=True)
        audio = self.tts.synthesize(script, folder / "voice.mp3")
        duration = self._duration(script, format_name)
        aligned = self.alignment.align(audio, script, duration)
        subtitles = folder / "captions.srt"
        self._write_subtitles(subtitles, aligned, duration)
        scenes = self._make_scene_cards(
            folder, title=title, topic=topic, format_name=format_name,
            storyboard=storyboard, background_path=background_path,
        )
        # Scene timing is based on the actual spoken edit duration, not the
        # placeholder storyboard duration.
        total_scene_time = sum(item["duration_seconds"] for item in scenes) or 1
        for item in scenes:
            item["duration_seconds"] = round(duration * item["duration_seconds"] / total_scene_time, 3)
        visual = folder / "visual-edit.mp4"
        source_suffix = Path(background_path).suffix.lower() if background_path else ""
        if background_path and source_suffix in {".mp4", ".mov", ".mkv", ".webm"}:
            self._gameplay_visual(ffmpeg, background_path, visual, duration, format_name)
        else:
            scene_videos: list[Path] = []
            for item in scenes:
                scene_path = folder / f"scene-{item['index']:02d}.mp4"
                self._scene_video(ffmpeg, item, scene_path, format_name)
                scene_videos.append(scene_path)
            self._concat_scenes(ffmpeg, scene_videos, visual)
        music = folder / "original-music-bed.wav"
        self._music_bed(ffmpeg, music, duration)
        output = folder / ("video-short.mp4" if format_name == "short" else "video-long.mp4")
        self._compose(ffmpeg, visual=visual, voice=audio, music=music, subtitles=subtitles, output=output, duration=duration, format_name=format_name)
        manifest = {
            "editor": "Orbit deterministic FFmpeg editor",
            "version": 2,
            "format": format_name,
            "resolution": {"width": (self.SHORT_SIZE if format_name == "short" else self.LONG_SIZE)[0], "height": (self.SHORT_SIZE if format_name == "short" else self.LONG_SIZE)[1]},
            "fps": self.FPS,
            "duration_seconds": duration,
            "voice": self.voice,
            "captions": {"path": str(subtitles), "word_aligned": bool(self.alignment.groq_api_key)},
            "audio": {"voice": str(audio), "original_music_bed": str(music), "music_volume": 0.10},
            "scenes": scenes,
            "safety": {"gameplay_downloaded": bool(background_path and Path(background_path).suffix.lower() in {'.mp4', '.mov', '.mkv', '.webm'}), "original_graphics": not bool(background_path and Path(background_path).suffix.lower() in {'.mp4', '.mov', '.mkv', '.webm'}), "background_path": background_path},
        }
        manifest_path = folder / "edit_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "path": str(output),
            "audio_path": str(audio),
            "music_path": str(music),
            "subtitles": str(subtitles),
            "manifest_path": str(manifest_path),
            "duration_seconds": duration,
            "width": manifest["resolution"]["width"],
            "height": manifest["resolution"]["height"],
            "edit_manifest": manifest,
        }

    def render_short(self, *, video_id: str, script: str, background_path: str | None = None, title: str = "Gaming myth ya fact?", topic: str = "gaming") -> dict[str, Any]:
        return self.render(video_id=video_id, script=script, title=title, topic=topic, format_name="short", background_path=background_path)

    def render_long(self, *, video_id: str, script: str, title: str = "Gaming deep dive", topic: str = "gaming", storyboard: list[dict[str, Any]] | None = None, background_path: str | None = None) -> dict[str, Any]:
        return self.render(video_id=video_id, script=script, title=title, topic=topic, format_name="long", storyboard=storyboard, background_path=background_path)
