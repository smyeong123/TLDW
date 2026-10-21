"""Text-to-speech: one audio file per script section, with word timings for captions.

`TTS` is the interface (swap in ElevenLabs/OpenAI later); EdgeTTS is the free default.
Durations come from ffprobe when available, else from the last word boundary.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import edge_tts

from tldw.script import Script
from tldw.usage import usage

TICKS_PER_SECOND = 10_000_000  # edge-tts offsets/durations are in 100 ns units


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Audio:
    path: Path
    duration_s: float
    words: list[Word] = field(default_factory=list)


class TTS(Protocol):
    def synthesize(self, text: str, voice: str, out_path: Path) -> Audio: ...


def probe_duration(path: Path) -> float | None:
    """Audio length in seconds via ffprobe, or None if ffprobe is missing/fails."""
    if shutil.which("ffprobe") is None:
        return None
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
        return float(json.loads(out)["format"]["duration"])
    except (subprocess.SubprocessError, OSError, KeyError, ValueError):
        return None


class EdgeTTS:
    def __init__(
        self,
        communicate: Callable[..., Any] = edge_tts.Communicate,
        probe: Callable[[Path], float | None] = probe_duration,
    ):
        self._communicate = communicate
        self._probe = probe

    def synthesize(self, text: str, voice: str, out_path: Path) -> Audio:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        words: list[Word] = []
        comm = self._communicate(text, voice, boundary="WordBoundary")
        with out_path.open("wb") as f:
            for chunk in comm.stream_sync():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / TICKS_PER_SECOND
                    end = start + chunk["duration"] / TICKS_PER_SECOND
                    words.append(Word(round(start, 3), round(end, 3), chunk["text"]))
        if out_path.stat().st_size == 0:
            raise RuntimeError(f"TTS produced no audio for voice {voice}")
        duration = self._probe(out_path) or (words[-1].end if words else 0.0)
        usage.add_tts(duration)
        return Audio(out_path, duration, words)


def section_files(script: Script, out_dir: Path) -> list[Path]:
    names = ["00_context", *(f"{i:02d}_point" for i in range(1, len(script.points) + 1))]
    names.append(f"{len(script.points) + 1:02d}_takeaway")
    return [out_dir / f"{n}.mp3" for n in names]


def synthesize_script(script: Script, voice: str, out_dir: Path, tts: TTS) -> list[Audio]:
    """One audio per section (context, each point, takeaway), in narration order."""
    return [
        tts.synthesize(text, voice, path)
        for text, path in zip(script.narration(), section_files(script, out_dir), strict=True)
    ]
