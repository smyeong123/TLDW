"""SQLite stage tracking so `tldw run` is idempotent across reruns.

Stages: new -> filtered_in | filtered_out -> transcribed -> scripted -> rendered
        -> delivered -> removed; any stage may go to failed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path


class Stage(StrEnum):
    NEW = "new"
    FILTERED_IN = "filtered_in"
    FILTERED_OUT = "filtered_out"
    TRANSCRIBED = "transcribed"
    SCRIPTED = "scripted"
    RENDERED = "rendered"
    DELIVERED = "delivered"
    REMOVED = "removed"
    FAILED = "failed"


# Legal forward transitions. FAILED is reachable from anywhere; retry resets explicitly.
_NEXT: dict[Stage, set[Stage]] = {
    Stage.NEW: {Stage.FILTERED_IN, Stage.FILTERED_OUT},
    Stage.FILTERED_IN: {Stage.TRANSCRIBED},
    Stage.FILTERED_OUT: set(),
    Stage.TRANSCRIBED: {Stage.SCRIPTED},
    Stage.SCRIPTED: {Stage.RENDERED},
    Stage.RENDERED: {Stage.DELIVERED},
    Stage.DELIVERED: {Stage.REMOVED},
    Stage.REMOVED: set(),
    Stage.FAILED: set(),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    stage            TEXT NOT NULL,
    topic            TEXT,
    error            TEXT,
    channel          TEXT,
    duration_s       INTEGER,
    playlist_item_id TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_videos_stage ON videos(stage);
"""


@dataclass(frozen=True)
class VideoRecord:
    id: str
    title: str
    stage: Stage
    topic: str | None
    error: str | None
    channel: str | None
    duration_s: int | None
    playlist_item_id: str | None
    created_at: str
    updated_at: str

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class IllegalTransition(RuntimeError):
    pass


class State:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> State:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- writes -------------------------------------------------------------

    def upsert_new(
        self,
        video_id: str,
        title: str,
        *,
        channel: str | None = None,
        duration_s: int | None = None,
        playlist_item_id: str | None = None,
    ) -> bool:
        """Insert a video as `new`. Returns True if inserted, False if it already existed.

        Existing rows only get their playlist_item_id refreshed (it changes if the user
        removes and re-adds the video), never their stage.
        """
        now = _now()
        cur = self._conn.execute(
            """INSERT OR IGNORE INTO videos
               (id, title, stage, channel, duration_s, playlist_item_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (video_id, title, Stage.NEW, channel, duration_s, playlist_item_id, now, now),
        )
        inserted = cur.rowcount == 1
        if not inserted and playlist_item_id is not None:
            self._conn.execute(
                "UPDATE videos SET playlist_item_id = ? WHERE id = ?",
                (playlist_item_id, video_id),
            )
        self._conn.commit()
        return inserted

    def set_stage(
        self,
        video_id: str,
        stage: Stage,
        *,
        topic: str | None = None,
        force: bool = False,
    ) -> None:
        """Advance a video to `stage`. Raises IllegalTransition unless force=True."""
        rec = self.get(video_id)
        if rec is None:
            raise KeyError(video_id)
        if not force and stage not in _NEXT[rec.stage]:
            raise IllegalTransition(f"{video_id}: {rec.stage} -> {stage} not allowed")
        self._conn.execute(
            "UPDATE videos SET stage = ?, topic = COALESCE(?, topic), error = NULL, "
            "updated_at = ? WHERE id = ?",
            (stage, topic, _now(), video_id),
        )
        self._conn.commit()

    def fail(self, video_id: str, error: str) -> None:
        """Mark as failed, keeping the previous stage in the error text for `tldw retry`."""
        rec = self.get(video_id)
        if rec is None:
            raise KeyError(video_id)
        self._conn.execute(
            "UPDATE videos SET stage = ?, error = ?, updated_at = ? WHERE id = ?",
            (Stage.FAILED, f"[at {rec.stage}] {error}"[:2000], _now(), video_id),
        )
        self._conn.commit()

    def reset(self, video_id: str, stage: Stage = Stage.NEW) -> None:
        """Explicitly put a video back to an earlier stage (used by `tldw retry`)."""
        self.set_stage(video_id, stage, force=True)

    # --- reads --------------------------------------------------------------

    def get(self, video_id: str) -> VideoRecord | None:
        row = self._conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        return _to_record(row) if row else None

    def list(self, stage: Stage | None = None) -> list[VideoRecord]:
        if stage is None:
            rows = self._conn.execute("SELECT * FROM videos ORDER BY created_at").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM videos WHERE stage = ? ORDER BY created_at", (stage,)
            ).fetchall()
        return [_to_record(r) for r in rows]

    def counts(self) -> dict[Stage, int]:
        rows = self._conn.execute("SELECT stage, COUNT(*) AS n FROM videos GROUP BY stage")
        return {Stage(r["stage"]): r["n"] for r in rows}


def _to_record(row: sqlite3.Row) -> VideoRecord:
    return VideoRecord(
        id=row["id"],
        title=row["title"],
        stage=Stage(row["stage"]),
        topic=row["topic"],
        error=row["error"],
        channel=row["channel"],
        duration_s=row["duration_s"],
        playlist_item_id=row["playlist_item_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
