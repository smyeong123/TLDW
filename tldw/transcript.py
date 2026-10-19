"""Captions for a video, cached to cache/<id>.json. Captions only, never audio/video streams.

Tier 1: youtube-transcript-api.  Tier 2: yt-dlp --skip-download (subtitle files only).
The plan's tier 3 (Whisper on audio) is off by the user's decision (captions only).

Track choice, for both tiers: the spoken language wins. Auto-generated captions are always
in the spoken language, so a manual track in that language is preferred, then the
auto-generated track, then any manual track.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import CouldNotRetrieveTranscript

log = logging.getLogger(__name__)


class NoCaptions(RuntimeError):
    pass


@dataclass(frozen=True)
class Segment:
    start: float  # seconds
    duration: float
    text: str


@dataclass(frozen=True)
class Transcript:
    video_id: str
    language: str
    is_generated: bool
    source: str  # "transcript-api" | "yt-dlp"
    segments: list[Segment] = field(default_factory=list)

    @property
    def end_s(self) -> float:
        return max((s.start + s.duration for s in self.segments), default=0.0)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> Transcript:
        d = json.loads(text)
        d["segments"] = [Segment(**s) for s in d["segments"]]
        return cls(**d)


def _clean(text: str) -> str:
    return " ".join(text.replace("\n", " ").split())


def _segments(raw: list[tuple[float, float, str]]) -> list[Segment]:
    return [Segment(round(s, 3), round(d, 3), _clean(t)) for s, d, t in raw if _clean(t)]


# --- tier 1: youtube-transcript-api -----------------------------------------------


def pick_track(tracks: list[tuple[str, bool]], hint: str | None = None) -> int | None:
    """Index of the best (language_code, is_generated) track, or None if there are none."""
    if not tracks:
        return None
    generated = [lang for lang, gen in tracks if gen]
    spoken = (generated[0] if generated else hint or "").split("-")[0].lower()
    for i, (lang, gen) in enumerate(tracks):
        if not gen and spoken and lang.split("-")[0].lower() == spoken:
            return i
    for i, (_, gen) in enumerate(tracks):
        if gen:
            return i
    return 0


def fetch_with_api(
    video_id: str, hint: str | None = None, api: Any | None = None
) -> Transcript | None:
    api = api or YouTubeTranscriptApi()
    try:
        listed = list(api.list(video_id))
    except CouldNotRetrieveTranscript as e:
        log.info("transcript-api: %s: %s", video_id, type(e).__name__)
        return None
    i = pick_track([(t.language_code, t.is_generated) for t in listed], hint)
    if i is None:
        return None
    chosen = listed[i]
    try:
        fetched = chosen.fetch()
    except CouldNotRetrieveTranscript as e:
        log.info("transcript-api fetch: %s: %s", video_id, type(e).__name__)
        return None
    segs = _segments([(s.start, s.duration, s.text) for s in fetched])
    return Transcript(video_id, chosen.language_code, chosen.is_generated, "transcript-api", segs)


# --- tier 2: yt-dlp (subtitles only) ------------------------------------------------

Runner = Callable[[list[str]], str]


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120).stdout


def pick_ytdlp_track(info: dict) -> tuple[str, bool] | None:
    """(track key, is_generated) from yt-dlp's info JSON."""
    manual: dict = info.get("subtitles") or {}
    auto: dict = info.get("automatic_captions") or {}
    orig = [k for k in auto if k.endswith("-orig")]  # auto track in the spoken language
    spoken = (orig[0][: -len("-orig")] if orig else info.get("language") or "").lower()
    manual = {k: v for k, v in manual.items() if k != "live_chat"}
    for k in manual:
        if spoken and k.split("-")[0].lower() == spoken.split("-")[0]:
            return k, False
    if orig:
        return orig[0], True
    if spoken and spoken in auto:
        return spoken, True
    if manual:
        return next(iter(manual)), False
    return None


def parse_json3(data: dict) -> list[Segment]:
    raw = []
    for ev in data.get("events", []):
        text = "".join(seg.get("utf8", "") for seg in ev.get("segs") or [])
        raw.append((ev.get("tStartMs", 0) / 1000, ev.get("dDurationMs", 0) / 1000, text))
    return _segments(raw)


def fetch_with_ytdlp(video_id: str, workdir: Path, run: Runner = _run) -> Transcript | None:
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        info = json.loads(run(["yt-dlp", "--skip-download", "--dump-single-json", url]))
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as e:
        log.info("yt-dlp info: %s: %s", video_id, e)
        return None
    picked = pick_ytdlp_track(info)
    if picked is None:
        return None
    key, generated = picked
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / f"{video_id}.ytdlp"
    flag = "--write-auto-subs" if generated else "--write-subs"
    try:
        run(
            [
                "yt-dlp",
                "--skip-download",
                flag,
                "--sub-langs",
                key,
                "--sub-format",
                "json3",
                "-o",
                str(out),
                url,
            ]
        )
        sub_file = workdir / f"{video_id}.ytdlp.{key}.json3"
        data = json.loads(sub_file.read_text(encoding="utf-8"))
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as e:
        log.info("yt-dlp subs: %s: %s", video_id, e)
        return None
    finally:
        for f in workdir.glob(f"{video_id}.ytdlp.*"):
            f.unlink(missing_ok=True)
    lang = key[: -len("-orig")] if key.endswith("-orig") else key
    return Transcript(video_id, lang, generated, "yt-dlp", parse_json3(data))


# --- entry point --------------------------------------------------------------------


def cache_path(cache_dir: Path, video_id: str) -> Path:
    return cache_dir / f"{video_id}.json"


def get_transcript(
    video_id: str,
    cache_dir: Path,
    *,
    hint: str | None = None,
    api: Any | None = None,
    run: Runner = _run,
) -> Transcript:
    """Cached transcript, else tier 1, else tier 2. Raises NoCaptions if both come up empty."""
    path = cache_path(cache_dir, video_id)
    if path.exists():
        return Transcript.from_json(path.read_text(encoding="utf-8"))
    t = fetch_with_api(video_id, hint, api)
    if t is None or not t.segments:
        t = fetch_with_ytdlp(video_id, cache_dir, run)
    if t is None or not t.segments:
        raise NoCaptions(f"{video_id}: no captions available (captions-only mode)")
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(t.to_json(), encoding="utf-8")
    return t
