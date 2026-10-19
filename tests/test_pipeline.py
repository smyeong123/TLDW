from datetime import UTC, datetime

import pytest

from tldw import pipeline
from tldw.config import Config
from tldw.state import State


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
