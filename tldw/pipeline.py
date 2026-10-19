"""Glue for `tldw run`: intake -> filter (-> later phases). Kept out of cli.py so it is testable."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import anthropic
from googleapiclient.discovery import Resource

from tldw import youtube
from tldw.classify import ClaudeClassifier, VideoInfo
from tldw.config import AppConfig, Config
from tldw.state import State


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
