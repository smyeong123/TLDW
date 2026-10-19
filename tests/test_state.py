import pytest

from tldw.state import IllegalTransition, Stage, State


@pytest.fixture
def state(tmp_path):
    with State(tmp_path / "state.db") as s:
        yield s


def test_upsert_is_idempotent(state):
    assert state.upsert_new("abc", "Title", channel="Chan", duration_s=600, playlist_item_id="p1")
    assert not state.upsert_new("abc", "Title", playlist_item_id="p2")
    rec = state.get("abc")
    assert rec.stage == Stage.NEW
    assert rec.playlist_item_id == "p2"  # refreshed
    assert rec.channel == "Chan"
    assert rec.url.endswith("watch?v=abc")


def test_happy_path_transitions(state):
    state.upsert_new("v", "t")
    for stage in [
        Stage.FILTERED_IN,
        Stage.TRANSCRIBED,
        Stage.SCRIPTED,
        Stage.RENDERED,
        Stage.DELIVERED,
        Stage.REMOVED,
    ]:
        state.set_stage("v", stage, topic="ML / AI" if stage == Stage.FILTERED_IN else None)
    rec = state.get("v")
    assert rec.stage == Stage.REMOVED
    assert rec.topic == "ML / AI"  # COALESCE keeps topic across later stages


def test_illegal_transition_rejected(state):
    state.upsert_new("v", "t")
    with pytest.raises(IllegalTransition):
        state.set_stage("v", Stage.RENDERED)
    state.set_stage("v", Stage.RENDERED, force=True)
    assert state.get("v").stage == Stage.RENDERED


def test_fail_and_retry(state):
    state.upsert_new("v", "t")
    state.set_stage("v", Stage.FILTERED_IN)
    state.fail("v", "boom")
    rec = state.get("v")
    assert rec.stage == Stage.FAILED
    assert rec.error.startswith("[at filtered_in] boom")
    state.reset("v", Stage.FILTERED_IN)
    assert state.get("v").stage == Stage.FILTERED_IN
    assert state.get("v").error is None


def test_list_and_counts(state):
    state.upsert_new("a", "t")
    state.upsert_new("b", "t")
    state.set_stage("b", Stage.FILTERED_OUT)
    assert [r.id for r in state.list(Stage.NEW)] == ["a"]
    assert state.counts() == {Stage.NEW: 1, Stage.FILTERED_OUT: 1}


def test_unknown_id(state):
    with pytest.raises(KeyError):
        state.set_stage("nope", Stage.FILTERED_IN)
