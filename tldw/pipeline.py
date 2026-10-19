"""Glue for `tldw run`: intake -> filter -> transcript -> script (-> later phases).

Kept out of cli.py so it is testable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import anthropic
from googleapiclient.discovery import Resource

from tldw import script as sc
from tldw import transcript as tr
from tldw import youtube
from tldw.classify import ClaudeClassifier, VideoInfo
from tldw.config import AppConfig, Config
from tldw.state import Stage, State


def snapshot_age_warning(state: State, cfg: Config, now: datetime | None = None) -> str | None:
    """A warning if the Watch Later snapshot is missing or stale, else None."""
    taken = state.latest_snapshot_at("watch_later")
    if taken is None:
        return (
            "no Watch Later snapshot yet: open youtube.com/playlist?list=WL and click "
            "'Send to TLDW' in the extension (or `tldw import <file>`)"
        )
    now = now or datetime.now(UTC)
    age = now - datetime.fromisoformat(taken)
    if age > timedelta(hours=cfg.snapshot_max_age_hours):
        hours = int(age.total_seconds() // 3600)
        return (
            f"newest Watch Later snapshot is {hours}h old (> {cfg.snapshot_max_age_hours}h); "
            "watched % may be out of date. Open Watch Later and send a fresh one."
        )
    return None


def optional_service(cfg: AppConfig) -> Resource | None:
    """YouTube client if `tldw auth` has been run, else None (watch_later works without it)."""
    try:
        creds = youtube.load_credentials(cfg.secrets)
    except youtube.AuthError:
        return None
    return youtube.build_service(creds) if creds else None


def video_infos(yt: Resource | None, ids: list[str]) -> dict[str, VideoInfo]:
    """Description/tags for the classifier; empty when there is no API access (title only)."""
    if yt is None or not ids:
        return {}
    details = youtube.fetch_video_details(yt, ids)
    return {
        vid: VideoInfo(vid, d.title, d.channel, d.description, d.tags) for vid, d in details.items()
    }


def make_classifier(cfg: AppConfig) -> ClaudeClassifier | None:
    """None when no topic filter is configured. Raises ValueError if the API key is missing."""
    if not cfg.config.topics:
        return None
    key = cfg.secrets.anthropic_api_key
    if not key:
        raise ValueError("topics are set but ANTHROPIC_API_KEY is missing from .env")
    return ClaudeClassifier(
        anthropic.Anthropic(api_key=key), cfg.config.llm_model, cfg.config.topics
    )


def anthropic_client(cfg: AppConfig) -> anthropic.Anthropic:
    key = cfg.secrets.anthropic_api_key
    if not key:
        raise ValueError("ANTHROPIC_API_KEY is missing from .env (needed for scripts)")
    return anthropic.Anthropic(api_key=key)


@dataclass(frozen=True)
class StepResult:
    video_id: str
    title: str
    step: str
    ok: bool
    message: str


def transcribe_step(
    state: State,
    cfg: AppConfig,
    *,
    limit: int,
    fetch: Callable[..., tr.Transcript] = tr.get_transcript,
) -> list[StepResult]:
    """filtered_in -> transcribed (captions cached in cache_dir); failures -> failed."""
    results = []
    for rec in state.list(Stage.FILTERED_IN)[:limit]:
        try:
            t = fetch(rec.id, cfg.cache_dir)
        except tr.NoCaptions as e:
            state.fail(rec.id, str(e))
            results.append(StepResult(rec.id, rec.title, "transcript", False, str(e)))
            continue
        state.set_stage(rec.id, Stage.TRANSCRIBED)
        msg = f"{len(t.segments)} segments, {t.language}, {t.source}"
        results.append(StepResult(rec.id, rec.title, "transcript", True, msg))
    return results


def script_step(
    state: State,
    cfg: AppConfig,
    client: anthropic.Anthropic,
    *,
    limit: int,
) -> list[StepResult]:
    """transcribed -> scripted (cache_dir/<id>.script.json); failures -> failed."""
    results = []
    for rec in state.list(Stage.TRANSCRIBED)[:limit]:
        path = tr.cache_path(cfg.cache_dir, rec.id)
        meta = sc.VideoMeta(rec.id, rec.title, rec.channel, rec.duration_s)
        try:
            t = tr.Transcript.from_json(path.read_text(encoding="utf-8"))
            script = sc.write_script(
                client,
                cfg.config.llm_model,
                meta,
                t,
                output_language=cfg.config.output_language,
            )
        except (OSError, sc.ScriptError, anthropic.APIError) as e:
            state.fail(rec.id, f"script: {e}")
            results.append(StepResult(rec.id, rec.title, "script", False, str(e)))
            continue
        sc.save(script, sc.script_path(cfg.cache_dir, rec.id))
        state.set_stage(rec.id, Stage.SCRIPTED)
        msg = f"{len(script.points)} points, {script.language}: {script.title}"
        results.append(StepResult(rec.id, rec.title, "script", True, msg))
    return results
