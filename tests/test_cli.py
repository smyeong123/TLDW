import textwrap

from typer.testing import CliRunner

from tldw import cli, youtube
from tldw.cli import fmt_duration
from tldw.state import Stage, State
from tldw.youtube import PlaylistEntry, VideoDetails

runner = CliRunner()


def test_fmt_duration():
    assert fmt_duration(None) == "-"
    assert fmt_duration(59) == "0:59"
    assert fmt_duration(3723) == "1:02:03"


def _write_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            playlist_name: TLDW
            topics: [ML]
            state_db: state.db
            """
        )
    )
    return cfg


def test_list_renders_table_with_stages(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path)
    with State(tmp_path / "state.db") as s:
        s.upsert_new("vid1", "Tracked one")
        s.set_stage("vid1", Stage.FILTERED_IN, topic="ML")

    monkeypatch.setattr(youtube, "require_service", lambda secrets: object())
    monkeypatch.setattr(youtube, "find_playlist_id", lambda yt, name: "PL123")
    monkeypatch.setattr(
        youtube,
        "iter_playlist_items",
        lambda yt, pid: iter(
            [
                PlaylistEntry("vid1", "pi1", "Tracked one", "Chan A", 0),
                PlaylistEntry("vid2", "pi2", "Fresh one", "Chan B", 1),
            ]
        ),
    )
    monkeypatch.setattr(
        youtube,
        "fetch_video_details",
        lambda yt, ids: {
            "vid1": VideoDetails("vid1", "Tracked one", "", "Chan A", [], 754, "en", ""),
            "vid2": VideoDetails("vid2", "Fresh one", "", "Chan B", [], 3600, "ko", ""),
        },
    )

    result = runner.invoke(cli.app, ["list", "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "Playlist 'TLDW' (2 videos)" in out
    assert "vid1" in out and "filtered_in" in out and "ML" in out
    assert "vid2" in out and "untracked" in out
    assert "12:34" in out and "1:00:00" in out


def test_list_missing_playlist_exits_4(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path)
    monkeypatch.setattr(youtube, "require_service", lambda secrets: object())
    monkeypatch.setattr(youtube, "find_playlist_id", lambda yt, name: None)
    result = runner.invoke(cli.app, ["list", "--config", str(cfg)])
    assert result.exit_code == 4
    assert "playlist not found" in result.output


def test_list_unauthenticated_exits_3(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path)
    monkeypatch.setattr("tldw.youtube.load_credentials", lambda secrets: None)
    result = runner.invoke(cli.app, ["list", "--config", str(cfg)])
    assert result.exit_code == 3
    assert "tldw auth" in result.output


def test_import_snapshot(tmp_path):
    import json

    cfg = _write_config(tmp_path)
    snap = tmp_path / "wl.json"
    item = {"video_id": "aaaaaaaaaaa", "title": "A", "watched_pct": 0.3}
    snap.write_text(
        json.dumps(
            {
                "version": 1,
                "source": "watch_later",
                "taken_at": "2026-10-20T01:00:00Z",
                "items": [item],
            }
        )
    )
    result = runner.invoke(cli.app, ["import", str(snap), "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    assert "1 new" in result.output
    with State(tmp_path / "state.db") as s:
        assert s.get("aaaaaaaaaaa").watched_pct == 0.3


def test_import_bad_file_exits_2(tmp_path):
    cfg = _write_config(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    result = runner.invoke(cli.app, ["import", str(bad), "--config", str(cfg)])
    assert result.exit_code == 2
    assert "import failed" in result.output


def test_serve_without_token_exits_2(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path)
    monkeypatch.delenv("TLDW_EXTENSION_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    result = runner.invoke(cli.app, ["serve", "--config", str(cfg)])
    assert result.exit_code == 2
    assert "TLDW_EXTENSION_TOKEN" in result.output


def _seed_snapshot(tmp_path, items):
    with State(tmp_path / "state.db") as s:
        for vid, pct in items:
            s.upsert_new(vid, f"title {vid}", watched_pct=pct, duration_s=600)
        s.record_snapshot("watch_later", "2026-10-20T00:00:00+00:00", len(items))


def _write_wl_config(tmp_path, topics="[]"):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"source: watch_later\ntopics: {topics}\nstate_db: state.db\n")
    return cfg


def test_run_dry_run_shows_decisions_and_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("tldw.pipeline.optional_service", lambda cfg: None)
    cfg = _write_wl_config(tmp_path)
    _seed_snapshot(tmp_path, [("aaaaaaaaaaa", 0.9), ("bbbbbbbbbbb", 0.1)])
    result = runner.invoke(cli.app, ["run", "--dry-run", "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    assert "watched" in result.output and "process" in result.output
    assert "process: 1, watched: 1" in result.output
    with State(tmp_path / "state.db") as s:
        assert {r.stage for r in s.list()} == {Stage.NEW}


def test_run_applies_decisions(tmp_path, monkeypatch):
    monkeypatch.setattr("tldw.pipeline.optional_service", lambda cfg: None)
    cfg = _write_wl_config(tmp_path)
    _seed_snapshot(tmp_path, [("aaaaaaaaaaa", 0.9), ("bbbbbbbbbbb", None)])
    result = runner.invoke(cli.app, ["run", "--until", "filtered", "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    with State(tmp_path / "state.db") as s:
        assert s.get("aaaaaaaaaaa").stage == Stage.WATCHED
        assert s.get("bbbbbbbbbbb").stage == Stage.FILTERED_IN


def test_run_with_topics_needs_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr("tldw.pipeline.optional_service", lambda cfg: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    cfg = _write_wl_config(tmp_path, topics="[ML]")
    _seed_snapshot(tmp_path, [("aaaaaaaaaaa", 0.1)])
    result = runner.invoke(cli.app, ["run", "--dry-run", "--config", str(cfg)])
    assert result.exit_code == 2
    assert "ANTHROPIC_API_KEY" in result.output


def test_run_warns_without_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr("tldw.pipeline.optional_service", lambda cfg: None)
    cfg = _write_wl_config(tmp_path)
    result = runner.invoke(cli.app, ["run", "--dry-run", "--config", str(cfg)])
    assert result.exit_code == 0
    assert "no Watch Later snapshot" in result.output
    assert "nothing pending" in result.output


def test_retry_resets_to_failed_stage(tmp_path):
    cfg = _write_config(tmp_path)
    with State(tmp_path / "state.db") as s:
        s.upsert_new("aaaaaaaaaaa", "A")
        s.set_stage("aaaaaaaaaaa", Stage.FILTERED_IN)
        s.fail("aaaaaaaaaaa", "no captions")
        s.upsert_new("bbbbbbbbbbb", "B")
    listing = runner.invoke(cli.app, ["retry", "--config", str(cfg)])
    assert "no captions" in listing.output and "tldw retry --all" in listing.output

    result = runner.invoke(cli.app, ["retry", "aaaaaaaaaaa", "bbbbbbbbbbb", "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    assert "aaaaaaaaaaa: back to filtered_in" in result.output
    assert "bbbbbbbbbbb: not a failed video" in result.output
    with State(tmp_path / "state.db") as s:
        assert s.get("aaaaaaaaaaa").stage == Stage.FILTERED_IN
        assert s.get("aaaaaaaaaaa").error is None


def test_retry_all_and_empty(tmp_path):
    cfg = _write_config(tmp_path)
    assert "No failed videos" in runner.invoke(cli.app, ["retry", "--config", str(cfg)]).output
    with State(tmp_path / "state.db") as s:
        s.upsert_new("aaaaaaaaaaa", "A")
        s.fail("aaaaaaaaaaa", "classifier error")
    runner.invoke(cli.app, ["retry", "--all", "--config", str(cfg)])
    with State(tmp_path / "state.db") as s:
        assert s.get("aaaaaaaaaaa").stage == Stage.NEW
