import pytest

from tldw import filter as flt
from tldw.classify import Classification
from tldw.config import Config
from tldw.state import Stage, State


@pytest.fixture
def state(tmp_path):
    with State(tmp_path / "state.db") as s:
        yield s


class FakeClassifier:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, video):
        self.calls.append(video.video_id)
        return self.result


def test_watched_skips_before_classifier(state):
    state.upsert_new("w", "Watched", watched_pct=0.6, duration_s=600)
    clf = FakeClassifier(Classification("ML", 0.9, "r"))
    [d] = flt.run_filter(state, Config(topics=["ML"]), clf)
    assert d.verdict == "watched" and "60%" in d.reason
    assert clf.calls == []
    rec = state.get("w")
    assert rec.stage == Stage.WATCHED and rec.note.startswith("watched 60%")


def test_threshold_is_inclusive_and_configurable(state):
    state.upsert_new("a", "A", watched_pct=0.5)
    state.upsert_new("b", "B", watched_pct=0.49)
    got = {d.video_id: d.verdict for d in flt.run_filter(state, Config(), None)}
    assert got == {"a": "watched", "b": "process"}

    state.upsert_new("c", "C", watched_pct=0.8)
    [d] = flt.run_filter(state, Config(watched_threshold=0.9), None)
    assert d.verdict == "process"


def test_unknown_progress_is_not_watched(state):
    state.upsert_new("u", "U")  # e.g. from the API playlist source
    [d] = flt.run_filter(state, Config(), None)
    assert d.verdict == "process" and d.reason == "no topic filter"


def test_too_long(state):
    state.upsert_new("l", "Long", duration_s=3 * 3600)
    [d] = flt.run_filter(state, Config(max_source_minutes=120), None)
    assert d.verdict == "too_long" and "180 min" in d.reason
    assert state.get("l").stage == Stage.FILTERED_OUT


def test_confidence_threshold(state):
    state.upsert_new("x", "X")
    clf = FakeClassifier(Classification("ML", 0.59, "meh"))
    [d] = flt.run_filter(state, Config(topics=["ML"], confidence_threshold=0.6), clf)
    assert d.verdict == "off_topic"
    rec = state.get("x")
    assert rec.stage == Stage.FILTERED_OUT and rec.topic == "ML"


def test_on_topic_filtered_in(state):
    state.upsert_new("y", "Y")
    clf = FakeClassifier(Classification("ML", 0.9, "about transformers"))
    [d] = flt.run_filter(state, Config(topics=["ML"]), clf)
    assert (d.verdict, d.topic) == ("process", "ML")
    assert state.get("y").stage == Stage.FILTERED_IN


def test_dry_run_changes_nothing(state):
    state.upsert_new("w", "W", watched_pct=0.9)
    state.upsert_new("n", "N")
    ds = flt.run_filter(state, Config(), None, dry_run=True)
    assert [d.verdict for d in ds] == ["watched", "process"]
    assert {r.stage for r in state.list()} == {Stage.NEW}


def test_filtered_in_then_watched_later(state):
    state.upsert_new("v", "V", watched_pct=0.1)
    flt.run_filter(state, Config(), None)
    assert state.get("v").stage == Stage.FILTERED_IN
    assert flt.run_filter(state, Config(), None) == []  # nothing pending

    state.upsert_new("v", "V", watched_pct=0.7)  # next snapshot: user watched it
    [d] = flt.run_filter(state, Config(), None)
    assert d.verdict == "watched"
    assert state.get("v").stage == Stage.WATCHED


def test_classifier_api_error_fails_video(state):
    import anthropic
    import httpx2

    def boom(video):
        raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x"))

    state.upsert_new("e", "E")
    [d] = flt.run_filter(state, Config(topics=["ML"]), boom)
    assert d.verdict == "error"
    assert state.get("e").stage == Stage.FAILED
