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
