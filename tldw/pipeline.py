"""Glue for `tldw run`: intake -> filter -> transcript -> script -> (render) -> deliver.

Kept out of cli.py so it is testable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anthropic
from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError

from tldw import script as sc
from tldw import transcript as tr
from tldw import youtube
from tldw.classify import ClaudeClassifier, VideoInfo
from tldw.config import AppConfig, Config
from tldw.deliver import common as dcommon
from tldw.deliver import telegram as dtelegram
from tldw.deliver import youtube as dyoutube
from tldw.state import Stage, State, VideoRecord


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
class Selection:
    """`tldw run --limit/--topic/--since`: which videos each step may pick up."""

    limit: int
    topic: str | None = None
    since: datetime | None = None  # only videos first seen at/after this time

    def pick(self, records: list[VideoRecord]) -> list[VideoRecord]:
        out = [
            r
            for r in records
            if (self.topic is None or (r.topic or "").casefold() == self.topic.casefold())
            and (self.since is None or datetime.fromisoformat(r.created_at) >= self.since)
        ]
        return out[: self.limit]


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
    sel: Selection,
    fetch: Callable[..., tr.Transcript] = tr.get_transcript,
) -> list[StepResult]:
    """filtered_in -> transcribed (captions cached in cache_dir); failures -> failed."""
    results = []
    for rec in sel.pick(state.list(Stage.FILTERED_IN)):
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
    sel: Selection,
) -> list[StepResult]:
    """transcribed -> scripted (cache_dir/<id>.script.json); failures -> failed."""
    results = []
    for rec in sel.pick(state.list(Stage.TRANSCRIBED)):
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


def video_path(cfg: AppConfig, video_id: str) -> Path:
    return cfg.out_dir / f"{video_id}.mp4"


@dataclass
class Channels:
    """How each delivery channel sends one short. Injected so tests need no network."""

    youtube: Callable[..., str] | None = None  # (path, title, description, language) -> id
    telegram: Callable[..., str] | None = None  # (path, caption) -> message id


def default_channels(cfg: AppConfig, yt: Resource | None) -> Channels:
    ch = Channels()
    if "youtube" in cfg.config.delivery and yt is not None:
        playlist: list[str] = []  # resolved on first upload (costs quota only when used)

        def upload(path: Path, title: str, description: str, language: str | None) -> str:
            if not playlist:
                playlist.append(dyoutube.ensure_playlist(yt, cfg.config.output_playlist_name))
            return dyoutube.upload_short(
                yt,
                path,
                title=title,
                description=description,
                language=language,
                playlist_id=playlist[0],
            )

        ch.youtube = upload
    token, chat = cfg.secrets.telegram_bot_token, cfg.secrets.telegram_chat_id
    if "telegram" in cfg.config.delivery and token and chat:

        def send(path: Path, caption: str) -> str:
            return str(dtelegram.send_video(token, chat, path, caption))

        ch.telegram = send
    return ch


def deliver_step(
    state: State,
    cfg: AppConfig,
    channels: Channels,
    *,
    sel: Selection,
    yt: Resource | None = None,
) -> list[StepResult]:
    """rendered -> delivered (every configured channel) -> removed (source: playlist only).

    Channels already done for a video are skipped, so a retry never double-posts. A YouTube
    quota error stops the step and leaves the remaining videos `rendered` for the next run.
    """
    results: list[StepResult] = []
    for rec in state.list(Stage.DELIVERED):  # removals deferred by an earlier quota stop
        msg = remove_source(state, cfg, rec.id, yt)
        if msg:
            results.append(StepResult(rec.id, rec.title, "remove", "removed" in msg, msg[2:]))
    for rec in sel.pick(state.list(Stage.RENDERED)):
        try:
            script = sc.load(sc.script_path(cfg.cache_dir, rec.id))
        except OSError as e:
            state.fail(rec.id, f"deliver: script missing: {e}")
            results.append(StepResult(rec.id, rec.title, "deliver", False, str(e)))
            continue
        mp4 = video_path(cfg, rec.id)
        title = dcommon.title(script, rec.topic, rec.title)
        done = state.deliveries(rec.id)
        error = None
        for channel in cfg.config.delivery:
            if channel in done:
                continue
            try:
                if channel == "youtube":
                    if channels.youtube is None:
                        raise RuntimeError("YouTube delivery needs `tldw auth` first")
                    desc = dcommon.description(script, rec.url, rec.title, rec.channel)
                    ref = channels.youtube(mp4, title, desc, script.language)
                else:
                    if channels.telegram is None:
                        raise RuntimeError("Telegram delivery needs TELEGRAM_BOT_TOKEN/CHAT_ID")
                    ref = channels.telegram(mp4, dcommon.telegram_caption(script, title, rec.url))
            except youtube.QuotaExceeded as e:
                results.append(StepResult(rec.id, rec.title, "deliver", False, f"{e}; stopping"))
                return results
            except (OSError, RuntimeError, dtelegram.TelegramError) as e:
                error = f"{channel}: {e}"
                break
            state.record_delivery(rec.id, channel, ref)
        if error:
            state.fail(rec.id, f"deliver: {error}")
            results.append(StepResult(rec.id, rec.title, "deliver", False, error))
            continue
        state.set_stage(rec.id, Stage.DELIVERED)
        msg = "delivered to " + ", ".join(cfg.config.delivery)
        msg += remove_source(state, cfg, rec.id, yt)
        results.append(StepResult(rec.id, rec.title, "deliver", True, msg))
    return results


def remove_source(state: State, cfg: AppConfig, video_id: str, yt: Resource | None) -> str:
    """Delete the source from the input playlist (API source only). Returns a message suffix."""
    rec = state.get(video_id)
    if cfg.config.source != "playlist" or rec is None or not rec.playlist_item_id or yt is None:
        return ""
    try:
        youtube.delete_playlist_item(yt, rec.playlist_item_id)
    except youtube.QuotaExceeded:
        return "; removal deferred (quota)"
    except HttpError as e:
        state.set_note(video_id, f"could not remove from playlist: {e}")
        return "; removal failed (see note)"
    state.set_stage(video_id, Stage.REMOVED)
    return f"; removed from '{cfg.config.playlist_name}'"
