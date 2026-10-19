"""Intake: get the list of saved videos into state.db as `new`.

Two sources, both normalized to IntakeItem:
  * watch_later - a JSON snapshot sent by the browser extension (`tldw serve` / `tldw import`)
  * playlist    - a user-created playlist read through the YouTube Data API
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from googleapiclient.discovery import Resource
from pydantic import BaseModel, Field, ValidationError, field_validator

from tldw import youtube
from tldw.state import State

SNAPSHOT_VERSION = 1


@dataclass(frozen=True)
class IntakeItem:
    video_id: str
    title: str
    channel: str | None = None
    duration_s: int | None = None
    watched_pct: float | None = None  # 0..1; None when the source can't tell
    playlist_item_id: str | None = None


@dataclass(frozen=True)
class IngestResult:
    new: int
    updated: int

    @property
    def total(self) -> int:
        return self.new + self.updated


class SnapshotError(ValueError):
    pass


# --- watch_later snapshot ---------------------------------------------------------


class _SnapshotItem(BaseModel):
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    title: str
    channel: str | None = None
    duration_s: int | None = Field(default=None, ge=0)
    watched_pct: float | None = Field(default=None, ge=0.0, le=1.0)


class _Snapshot(BaseModel):
    version: Literal[1]
    source: Literal["watch_later"]
    taken_at: datetime
    items: list[_SnapshotItem]

    @field_validator("taken_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return v if v.tzinfo else v.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Snapshot:
    taken_at: str  # ISO 8601 UTC, seconds precision
    items: list[IntakeItem]


def parse_snapshot(data: Any) -> Snapshot:
    """Validate the extension's JSON payload. Raises SnapshotError with a readable reason."""
    try:
        snap = _Snapshot.model_validate(data)
    except ValidationError as e:
        raise SnapshotError(
            f"invalid snapshot: {e.errors()[0]['msg']} at {e.errors()[0]['loc']}"
        ) from None
    # The page lists a video once; keep the first if the extension ever sends a duplicate.
    seen: dict[str, IntakeItem] = {}
    for it in snap.items:
        seen.setdefault(
            it.video_id,
            IntakeItem(it.video_id, it.title, it.channel, it.duration_s, it.watched_pct),
        )
    taken_at = snap.taken_at.astimezone(UTC).replace(microsecond=0).isoformat()
    return Snapshot(taken_at=taken_at, items=list(seen.values()))


def ingest_snapshot(state: State, data: Any) -> IngestResult:
    """Parse + store a Watch Later snapshot. The single entry point for serve and import."""
    snap = parse_snapshot(data)
    result = ingest(state, snap.items)
    state.record_snapshot("watch_later", snap.taken_at, len(snap.items))
    return result


# --- playlist via the API -----------------------------------------------------------


class PlaylistNotFound(LookupError):
    pass


def from_playlist(yt: Resource, playlist_name: str) -> list[IntakeItem]:
    """Read every video in the named playlist (watched % is unknown through the API)."""
    playlist_id = youtube.find_playlist_id(yt, playlist_name)
    if playlist_id is None:
        raise PlaylistNotFound(playlist_name)
    entries = list(youtube.iter_playlist_items(yt, playlist_id))
    details = youtube.fetch_video_details(yt, [e.video_id for e in entries])
    items = []
    for e in entries:
        d = details.get(e.video_id)
        items.append(
            IntakeItem(
                video_id=e.video_id,
                title=d.title if d else e.title,
                channel=(d.channel if d else None) or e.channel,
                duration_s=d.duration_s if d else None,
                playlist_item_id=e.playlist_item_id,
            )
        )
    return items


# --- shared -------------------------------------------------------------------------


def ingest(state: State, items: list[IntakeItem]) -> IngestResult:
    """Upsert items as `new`; existing rows only get refreshed metadata, never a new stage."""
    new = 0
    for it in items:
        if state.upsert_new(
            it.video_id,
            it.title,
            channel=it.channel,
            duration_s=it.duration_s,
            playlist_item_id=it.playlist_item_id,
            watched_pct=it.watched_pct,
        ):
            new += 1
    return IngestResult(new=new, updated=len(items) - new)
