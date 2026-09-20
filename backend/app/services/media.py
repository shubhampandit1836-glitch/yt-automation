"""TTS and deterministic FFmpeg rendering for the first real publishing path."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any


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
        asyncio.run(self._synthesize(text, output))
        return output


class RenderService:
    """Render a clean original 9:16 video without downloading game footage.

    The default is deliberately conservative: a branded colour canvas, TTS,
    and timed subtitles. More visual asset workers can be added without
    changing the YouTube upload contract.
    """

    def __init__(self, media_dir: str, voice: str = "hi-IN-SwaraNeural") -> None:
        self.media_dir = Path(media_dir)
        self.tts = TTSService(voice)

    @staticmethod
    def _duration(text: str) -> float:
        # Indian-English speech varies by voice; this gives the renderer a safe
        # minimum and lets ffprobe/QA reject a result that exceeds the format.
        words = max(1, len(text.split()))
        return max(8.0, min(58.0, words / 2.25))

    def render_short(self, *, video_id: str, script: str) -> dict[str, Any]:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise MediaNotConfigured("FFmpeg is not installed; install it with apt install ffmpeg")
        folder = self.media_dir / video_id
        folder.mkdir(parents=True, exist_ok=True)
        audio = self.tts.synthesize(script, folder / "voice.mp3")
        output = folder / "video.mp4"
        subtitles = folder / "captions.srt"
        duration = self._duration(script)
        words = script.split()
        chunk_size = 4
        lines = [" ".join(words[index : index + chunk_size]) for index in range(0, len(words), chunk_size)]
        step = duration / max(1, len(lines))
        def stamp(seconds: float) -> str:
            whole = int(seconds)
            return f"00:{whole // 60:02d}:{whole % 60:02d},{int((seconds % 1) * 1000):03d}"
        subtitles.write_text(
            "\n\n".join(
                f"{index + 1}\n{stamp(index * step)} --> {stamp(min(duration, (index + 1) * step))}\n{line}"
                for index, line in enumerate(lines)
            ),
            encoding="utf-8",
        )
        subtitle_filter = str(subtitles).replace("\\", "/").replace(":", "\\:")
        command = [
            ffmpeg,
            "-y",
            "-f", "lavfi",
            "-i", "color=c=0x10231b:s=1080x1920:r=30",
            "-i", str(audio),
            "-vf", f"subtitles='{subtitle_filter}':force_style='FontName=DejaVu Sans,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H0010231B,BorderStyle=3,Outline=2,MarginV=260'",
            "-t", f"{duration:.2f}",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            str(output),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise MediaNotConfigured(f"FFmpeg render failed: {result.stderr[-800:]}")
        return {"path": str(output), "audio_path": str(audio), "duration_seconds": duration, "subtitles": str(subtitles)}
