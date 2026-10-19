from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tldw import pipeline
from tldw import script as sc
from tldw.config import AppConfig, Config, Secrets
from tldw.state import Stage, State
from tldw.transcript import NoCaptions, Segment, Transcript


@pytest.fixture
def state(tmp_path):
    with State(tmp_path / "state.db") as s:
        yield s


NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def test_snapshot_warning_when_missing(state):
    assert "no Watch Later snapshot" in pipeline.snapshot_age_warning(state, Config(), NOW)


def test_snapshot_warning_when_stale(state):
    state.record_snapshot("watch_later", "2026-10-17T12:00:00+00:00", 3)
    warning = pipeline.snapshot_age_warning(state, Config(snapshot_max_age_hours=48), NOW)
    assert "72h old" in warning


def test_no_warning_when_fresh(state):
    state.record_snapshot("watch_later", "2026-10-20T00:00:00+00:00", 3)
    assert pipeline.snapshot_age_warning(state, Config(), NOW) is None


def test_video_infos_without_api_is_empty():
    assert pipeline.video_infos(None, ["a"]) == {}


# --- transcript / script steps ------------------------------------------------------


def _appcfg(tmp_path):
    return AppConfig(config=Config(), secrets=Secrets(_env_file=None), root=tmp_path)


def _transcript(vid):
    return Transcript(vid, "en", True, "transcript-api", [Segment(0, 5, "hello")])


def test_transcribe_step(tmp_path, state):
    cfg = _appcfg(tmp_path)
    for vid in ("aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"):
        state.upsert_new(vid, vid)
        state.set_stage(vid, Stage.FILTERED_IN)

    def fetch(vid, cache_dir):
        if vid == "bbbbbbbbbbb":
            raise NoCaptions("no captions")
        return _transcript(vid)

    results = pipeline.transcribe_step(state, cfg, limit=2, fetch=fetch)
    assert [(r.video_id, r.ok) for r in results] == [("aaaaaaaaaaa", True), ("bbbbbbbbbbb", False)]
    assert state.get("aaaaaaaaaaa").stage == Stage.TRANSCRIBED
    assert state.get("bbbbbbbbbbb").stage == Stage.FAILED
    assert state.get("ccccccccccc").stage == Stage.FILTERED_IN  # over the limit


def test_script_step(tmp_path, state, monkeypatch):
    cfg = _appcfg(tmp_path)
    cfg.cache_dir.mkdir()
    state.upsert_new("aaaaaaaaaaa", "Title", duration_s=600)
    state.set_stage("aaaaaaaaaaa", Stage.FILTERED_IN)
    state.set_stage("aaaaaaaaaaa", Stage.TRANSCRIBED)
    (cfg.cache_dir / "aaaaaaaaaaa.json").write_text(_transcript("aaaaaaaaaaa").to_json())
    state.upsert_new("bbbbbbbbbbb", "No transcript file")
    state.set_stage("bbbbbbbbbbb", Stage.FILTERED_IN)
    state.set_stage("bbbbbbbbbbb", Stage.TRANSCRIBED)

    script = sc.Script(
        title="T",
        language="en",
        context="c.",
        points=[sc.Point(claim="a", detail="b", timestamp_s=1)] * 3,
        takeaway="t.",
    )
    monkeypatch.setattr(sc, "write_script", lambda *a, **k: script)
    results = pipeline.script_step(state, cfg, SimpleNamespace(), limit=5)
    assert [(r.video_id, r.ok) for r in results] == [("aaaaaaaaaaa", True), ("bbbbbbbbbbb", False)]
    assert state.get("aaaaaaaaaaa").stage == Stage.SCRIPTED
    assert sc.load(sc.script_path(cfg.cache_dir, "aaaaaaaaaaa")) == script
    assert state.get("bbbbbbbbbbb").stage == Stage.FAILED


def test_anthropic_client_needs_key(tmp_path):
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        pipeline.anthropic_client(_appcfg(tmp_path))
