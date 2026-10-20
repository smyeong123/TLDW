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

    results = pipeline.transcribe_step(state, cfg, sel=pipeline.Selection(2), fetch=fetch)
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
    results = pipeline.script_step(state, cfg, SimpleNamespace(), sel=pipeline.Selection(5))
    assert [(r.video_id, r.ok) for r in results] == [("aaaaaaaaaaa", True), ("bbbbbbbbbbb", False)]
    assert state.get("aaaaaaaaaaa").stage == Stage.SCRIPTED
    assert sc.load(sc.script_path(cfg.cache_dir, "aaaaaaaaaaa")) == script
    assert state.get("bbbbbbbbbbb").stage == Stage.FAILED


def test_anthropic_client_needs_key(tmp_path):
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        pipeline.anthropic_client(_appcfg(tmp_path))


# --- deliver step -------------------------------------------------------------------


def _rendered(state, cfg, vid, *, playlist_item_id=None):
    state.upsert_new(vid, f"Orig {vid}", channel="Chan", playlist_item_id=playlist_item_id)
    for st in (Stage.FILTERED_IN, Stage.TRANSCRIBED, Stage.SCRIPTED, Stage.RENDERED):
        state.set_stage(vid, st, topic="ML" if st == Stage.FILTERED_IN else None)
    script = sc.Script(
        title="Short",
        language="en",
        context="c.",
        points=[sc.Point(claim="a.", detail="b", timestamp_s=1)] * 3,
        takeaway="t.",
    )
    sc.save(script, sc.script_path(cfg.cache_dir, vid))
    pipeline.video_path(cfg, vid).parent.mkdir(parents=True, exist_ok=True)
    pipeline.video_path(cfg, vid).write_bytes(b"mp4")


def _cfg(tmp_path, **config):
    return AppConfig(config=Config(**config), secrets=Secrets(_env_file=None), root=tmp_path)


def test_deliver_both_channels(tmp_path, state):
    cfg = _cfg(tmp_path, delivery=["youtube", "telegram"])
    _rendered(state, cfg, "aaaaaaaaaaa")
    sent = []
    ch = pipeline.Channels(
        youtube=lambda path, title, desc, lang: sent.append(("yt", title, lang)) or "YT1",
        telegram=lambda path, caption: sent.append(("tg", caption)) or "77",
    )
    [r] = pipeline.deliver_step(state, cfg, ch, sel=pipeline.Selection(5))
    assert r.ok
    assert sent[0] == ("yt", '[ML] Short — TLDW of "Orig aaaaaaaaaaa"', "en")
    assert sent[1][0] == "tg" and "watch?v=aaaaaaaaaaa" in sent[1][1]
    assert state.get("aaaaaaaaaaa").stage == Stage.DELIVERED  # watch_later: no removal
    assert state.deliveries("aaaaaaaaaaa") == {"youtube": "YT1", "telegram": "77"}


def test_deliver_retry_skips_done_channel(tmp_path, state):
    cfg = _cfg(tmp_path, delivery=["youtube", "telegram"])
    _rendered(state, cfg, "aaaaaaaaaaa")
    uploads = []

    def tg_fail(path, caption):
        raise pipeline.dtelegram.TelegramError("down")

    ch = pipeline.Channels(youtube=lambda *a: uploads.append(1) or "YT1", telegram=tg_fail)
    [r] = pipeline.deliver_step(state, cfg, ch, sel=pipeline.Selection(5))
    assert not r.ok and state.failed_at("aaaaaaaaaaa") == Stage.RENDERED

    state.reset("aaaaaaaaaaa", Stage.RENDERED)
    ch.telegram = lambda path, caption: "9"
    [r] = pipeline.deliver_step(state, cfg, ch, sel=pipeline.Selection(5))
    assert r.ok and uploads == [1]  # YouTube not uploaded twice


def test_deliver_quota_stops_and_keeps_rendered(tmp_path, state):
    cfg = _cfg(tmp_path, delivery=["youtube"])
    _rendered(state, cfg, "aaaaaaaaaaa")
    _rendered(state, cfg, "bbbbbbbbbbb")

    def quota(*a):
        raise pipeline.youtube.QuotaExceeded("quota")

    results = pipeline.deliver_step(
        state, cfg, pipeline.Channels(youtube=quota), sel=pipeline.Selection(5)
    )
    assert len(results) == 1 and "stopping" in results[0].message
    assert {r.stage for r in state.list()} == {Stage.RENDERED}


def test_deliver_missing_channel_fails(tmp_path, state):
    cfg = _cfg(tmp_path, delivery=["telegram"])
    _rendered(state, cfg, "aaaaaaaaaaa")
    [r] = pipeline.deliver_step(state, cfg, pipeline.Channels(), sel=pipeline.Selection(5))
    assert not r.ok and "TELEGRAM_BOT_TOKEN" in r.message


def test_deliver_removes_source_for_playlist(tmp_path, state, monkeypatch):
    cfg = _cfg(tmp_path, delivery=["telegram"], source="playlist")
    _rendered(state, cfg, "aaaaaaaaaaa", playlist_item_id="PI1")
    deleted = []
    monkeypatch.setattr(pipeline.youtube, "delete_playlist_item", lambda yt, pi: deleted.append(pi))
    ch = pipeline.Channels(telegram=lambda path, caption: "1")
    [r] = pipeline.deliver_step(state, cfg, ch, sel=pipeline.Selection(5), yt=object())
    assert r.ok and "removed from 'TLDW'" in r.message
    assert deleted == ["PI1"]
    assert state.get("aaaaaaaaaaa").stage == Stage.REMOVED


def test_deferred_removal_retried(tmp_path, state, monkeypatch):
    cfg = _cfg(tmp_path, delivery=["telegram"], source="playlist")
    _rendered(state, cfg, "aaaaaaaaaaa", playlist_item_id="PI1")

    def quota(yt, pi):
        raise pipeline.youtube.QuotaExceeded("q")

    monkeypatch.setattr(pipeline.youtube, "delete_playlist_item", quota)
    ch = pipeline.Channels(telegram=lambda path, caption: "1")
    [r] = pipeline.deliver_step(state, cfg, ch, sel=pipeline.Selection(5), yt=object())
    assert "deferred" in r.message and state.get("aaaaaaaaaaa").stage == Stage.DELIVERED

    monkeypatch.setattr(pipeline.youtube, "delete_playlist_item", lambda yt, pi: None)
    [r] = pipeline.deliver_step(state, cfg, ch, sel=pipeline.Selection(5), yt=object())
    assert r.step == "remove" and r.ok
    assert state.get("aaaaaaaaaaa").stage == Stage.REMOVED


def test_selection_filters_topic_since_and_limit(state):
    for vid, topic in (("a", "ML / AI"), ("b", "Finance"), ("c", "ml / ai"), ("d", "ML / AI")):
        state.upsert_new(vid, vid)
        state.set_stage(vid, Stage.FILTERED_IN, topic=topic)
    recs = state.list(Stage.FILTERED_IN)
    assert [r.id for r in pipeline.Selection(2, topic="ML / AI").pick(recs)] == ["a", "c"]
    assert [r.id for r in pipeline.Selection(10).pick(recs)] == ["a", "b", "c", "d"]
    future = datetime(2100, 1, 1, tzinfo=UTC)
    assert pipeline.Selection(10, since=future).pick(recs) == []
