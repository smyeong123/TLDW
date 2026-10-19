import pytest

from tldw import intake, youtube
from tldw.intake import IntakeItem, SnapshotError
from tldw.state import Stage, State
from tldw.youtube import PlaylistEntry, VideoDetails


@pytest.fixture
def state(tmp_path):
    with State(tmp_path / "state.db") as s:
        yield s


def _snapshot(items, taken_at="2026-10-20T01:00:00Z"):
    return {"version": 1, "source": "watch_later", "taken_at": taken_at, "items": items}


ITEM_A = {
    "video_id": "aaaaaaaaaaa",
    "title": "A",
    "channel": "Chan",
    "duration_s": 600,
    "watched_pct": 0.25,
}


def test_parse_snapshot_normalizes_and_dedupes():
    snap = intake.parse_snapshot(
        _snapshot([ITEM_A, {**ITEM_A, "title": "dup"}], taken_at="2026-10-20T10:00:00+09:00")
    )
    assert snap.taken_at == "2026-10-20T01:00:00+00:00"
    assert snap.items == [IntakeItem("aaaaaaaaaaa", "A", "Chan", 600, 0.25)]


@pytest.mark.parametrize(
    "bad",
    [
        {"version": 2, "source": "watch_later", "taken_at": "2026-10-20T00:00:00Z", "items": []},
        _snapshot([{**ITEM_A, "watched_pct": 1.5}]),
        _snapshot([{**ITEM_A, "video_id": "short"}]),
        "not a dict",
    ],
)
def test_parse_snapshot_rejects_bad_input(bad):
    with pytest.raises(SnapshotError):
        intake.parse_snapshot(bad)


def test_ingest_snapshot_inserts_then_refreshes(state):
    r1 = intake.ingest_snapshot(state, _snapshot([ITEM_A]))
    assert (r1.new, r1.updated) == (1, 0)
    state.set_stage("aaaaaaaaaaa", Stage.FILTERED_IN)

    r2 = intake.ingest_snapshot(
        state, _snapshot([{**ITEM_A, "watched_pct": 0.8}], taken_at="2026-10-21T01:00:00Z")
    )
    assert (r2.new, r2.updated) == (0, 1)
    rec = state.get("aaaaaaaaaaa")
    assert rec.stage == Stage.FILTERED_IN  # stage untouched by re-ingest
    assert rec.watched_pct == 0.8
    assert state.latest_snapshot_at("watch_later") == "2026-10-21T01:00:00+00:00"


def test_from_playlist_merges_details(monkeypatch):
    monkeypatch.setattr(youtube, "find_playlist_id", lambda yt, name: "PL")
    monkeypatch.setattr(
        youtube,
        "iter_playlist_items",
        lambda yt, pid: iter([PlaylistEntry("v1", "pi1", "T1", "Owner", 0)]),
    )
    monkeypatch.setattr(
        youtube,
        "fetch_video_details",
        lambda yt, ids: {"v1": VideoDetails("v1", "T1", "", "Chan", [], 300, "en", "")},
    )
    assert intake.from_playlist(object(), "TLDW") == [
        IntakeItem("v1", "T1", "Chan", 300, None, "pi1")
    ]


def test_from_playlist_missing(monkeypatch):
    monkeypatch.setattr(youtube, "find_playlist_id", lambda yt, name: None)
    with pytest.raises(intake.PlaylistNotFound):
        intake.from_playlist(object(), "TLDW")


def test_state_migrates_old_schema(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE videos (id TEXT PRIMARY KEY, title TEXT NOT NULL, stage TEXT NOT NULL, "
        "topic TEXT, error TEXT, channel TEXT, duration_s INTEGER, playlist_item_id TEXT, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()
    with State(db) as s:
        s.upsert_new("v", "t", watched_pct=0.1)
        assert s.get("v").watched_pct == 0.1
