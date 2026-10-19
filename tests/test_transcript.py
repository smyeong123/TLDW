import json
import subprocess
from types import SimpleNamespace

import pytest
from youtube_transcript_api._errors import TranscriptsDisabled

from tldw import transcript as tr
from tldw.transcript import NoCaptions, Segment, Transcript

VID = "aaaaaaaaaaa"


def test_pick_track_prefers_manual_in_spoken_language():
    # auto track says spoken language is ko; manual en is a translation
    tracks = [("en", False), ("ko", True), ("ko-KR", False)]
    assert tr.pick_track(tracks) == 2
    assert tr.pick_track([("en", False), ("ko", True)]) == 1
    assert tr.pick_track([("fr", False), ("de", False)], hint="de") == 1
    assert tr.pick_track([("fr", False)]) == 0
    assert tr.pick_track([]) is None


class _Track:
    def __init__(self, lang, generated, snippets):
        self.language_code = lang
        self.is_generated = generated
        self._snippets = snippets

    def fetch(self):
        return [SimpleNamespace(text=t, start=s, duration=d) for s, d, t in self._snippets]


class _Api:
    def __init__(self, tracks=None, error=None):
        self.tracks = tracks or []
        self.error = error

    def list(self, video_id):
        if self.error:
            raise self.error
        return self.tracks


def test_fetch_with_api_cleans_text():
    api = _Api(
        [_Track("en", True, [(0.0, 2.0, "hello\nworld"), (2.0, 1.5, "  "), (3.5, 2, "bye")])]
    )
    t = tr.fetch_with_api(VID, api=api)
    assert t.language == "en" and t.is_generated and t.source == "transcript-api"
    assert t.segments == [Segment(0.0, 2.0, "hello world"), Segment(3.5, 2.0, "bye")]
    assert t.end_s == 5.5


def test_fetch_with_api_disabled_returns_none():
    assert tr.fetch_with_api(VID, api=_Api(error=TranscriptsDisabled(VID))) is None


def test_pick_ytdlp_track():
    info = {
        "language": "ko",
        "subtitles": {"en": [], "live_chat": []},
        "automatic_captions": {"ko-orig": [], "ko": [], "en": []},
    }
    assert tr.pick_ytdlp_track(info) == ("ko-orig", True)
    info["subtitles"]["ko"] = []
    assert tr.pick_ytdlp_track(info) == ("ko", False)
    assert tr.pick_ytdlp_track({"subtitles": {}, "automatic_captions": {}}) is None


def test_parse_json3():
    data = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 1500, "segs": [{"utf8": "Hi "}, {"utf8": "there"}]},
            {"tStartMs": 1500, "dDurationMs": 500, "segs": [{"utf8": "\n"}]},
            {"tStartMs": 2000},
        ]
    }
    assert tr.parse_json3(data) == [Segment(0.0, 1.5, "Hi there")]


def test_fetch_with_ytdlp_never_downloads_media(tmp_path):
    calls = []

    def run(cmd):
        calls.append(cmd)
        if "--dump-single-json" in cmd:
            return json.dumps({"automatic_captions": {"en-orig": []}, "subtitles": {}})
        out = cmd[cmd.index("-o") + 1]
        sub = {"events": [{"tStartMs": 1000, "dDurationMs": 2000, "segs": [{"utf8": "yo"}]}]}
        (tmp_path / f"{out.rsplit('/', 1)[1]}.en-orig.json3").write_text(json.dumps(sub))
        return ""

    t = tr.fetch_with_ytdlp(VID, tmp_path, run)
    assert t == Transcript(VID, "en", True, "yt-dlp", [Segment(1.0, 2.0, "yo")])
    assert all("--skip-download" in c for c in calls)
    assert list(tmp_path.iterdir()) == []  # temp subtitle file cleaned up


def test_fetch_with_ytdlp_failure_is_none(tmp_path):
    def run(cmd):
        raise subprocess.CalledProcessError(1, cmd)

    assert tr.fetch_with_ytdlp(VID, tmp_path, run) is None


def test_get_transcript_caches_and_falls_back(tmp_path):
    def run(cmd):
        raise AssertionError("yt-dlp should not run when tier 1 works")

    api = _Api([_Track("en", False, [(0, 1, "one")])])
    t = tr.get_transcript(VID, tmp_path, api=api, run=run)
    assert (tmp_path / f"{VID}.json").exists()
    # second call is served from cache even with a broken API
    again = tr.get_transcript(VID, tmp_path, api=_Api(error=TranscriptsDisabled(VID)), run=run)
    assert again == t


def test_get_transcript_no_captions(tmp_path):
    def run(cmd):
        return json.dumps({"subtitles": {}, "automatic_captions": {}})

    with pytest.raises(NoCaptions):
        tr.get_transcript(VID, tmp_path, api=_Api(error=TranscriptsDisabled(VID)), run=run)
