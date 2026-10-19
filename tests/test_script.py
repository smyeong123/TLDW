import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tldw import script as sc
from tldw.script import Point, Script, ScriptError, VideoMeta
from tldw.transcript import Segment, Transcript

FIXTURE = Path(__file__).parent / "fixtures" / "transcript_en.json"


@pytest.fixture
def transcript():
    return Transcript.from_json(FIXTURE.read_text())


META = VideoMeta("aaaaaaaaaaa", "How CPUs cache memory", "Chan", 600)


def _words(n):
    return " ".join(["word"] * n)


def make_script(n_points=4, words_per_point=40, ts=60, language="en", context=None):
    return Script(
        title="CPU caches in five minutes",
        language=language,
        context=context or "A systems talk by Chan. It explains why caches matter.",
        points=[
            Point(claim=f"Claim {i}.", detail=_words(words_per_point), timestamp_s=ts * i)
            for i in range(1, n_points + 1)
        ],
        takeaway="Keep hot data small.",
    )


def test_transcript_text_merges_blocks(transcript):
    text = sc.transcript_text(transcript, chunk_s=120)
    lines = text.splitlines()
    assert lines[0].startswith("[0:00] ")
    assert all(line.startswith("[") for line in lines)
    assert len(lines) < len(transcript.segments)


def test_fmt_ts():
    assert sc.fmt_ts(59) == "0:59"
    assert sc.fmt_ts(3723) == "1:02:03"


def test_estimate_seconds():
    assert sc.estimate_seconds(_words(150), "en") == 60
    assert sc.estimate_seconds("가" * 65, "ko") == pytest.approx(10)


def test_validate_ok():
    assert sc.validate(make_script(), duration_s=600, language="en") == []


@pytest.mark.parametrize(
    "s, needle",
    [
        (make_script(n_points=2, words_per_point=80), "3-5 points"),
        (make_script(ts=200), "outside the video"),
        (make_script(words_per_point=5), "lengthen"),
        (make_script(words_per_point=120), "shorten"),
        (make_script(language="ko"), "must be written in 'en'"),
        (make_script(context="One. Two. Three."), "at most 2 sentences"),
    ],
)
def test_validate_problems(s, needle):
    problems = sc.validate(s, duration_s=600, language="en")
    assert any(needle in p for p in problems), problems


class _Messages:
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = []

    def parse(self, **kw):
        self.calls.append(kw)
        s = self.scripts.pop(0)
        return SimpleNamespace(stop_reason="end_turn" if s else "refusal", parsed_output=s)


def _client(*scripts):
    return SimpleNamespace(messages=_Messages(scripts))


def test_write_script_first_try(transcript):
    client = _client(make_script())
    s = sc.write_script(client, "claude-opus-5-5", META, transcript)
    assert s.title == "CPU caches in five minutes"
    kw = client.messages.calls[0]
    assert kw["output_format"] is Script
    assert "Write the script in language: en" in kw["messages"][0]["content"]
    assert "[0:00]" in kw["messages"][0]["content"]


def test_write_script_retries_with_feedback(transcript):
    client = _client(make_script(n_points=2, words_per_point=80), make_script())
    sc.write_script(client, "m", META, transcript)
    retry = client.messages.calls[1]["messages"]
    assert [m["role"] for m in retry] == ["user", "assistant", "user"]
    assert "3-5 points" in retry[2]["content"]


def test_write_script_gives_up_after_retry(transcript):
    bad = make_script(n_points=2, words_per_point=80)
    with pytest.raises(ScriptError, match="invalid script after retry"):
        sc.write_script(_client(bad, bad), "m", META, transcript)


def test_write_script_refusal(transcript):
    with pytest.raises(ScriptError, match="refusal"):
        sc.write_script(_client(None), "m", META, transcript)


def test_output_language_override():
    t = Transcript("x", "ko", True, "transcript-api", [Segment(0, 1, "안녕")])
    assert sc.target_language("match", t) == "ko"
    assert sc.target_language("en", t) == "en"


def test_save_load_roundtrip(tmp_path):
    s = make_script()
    p = sc.script_path(tmp_path, "aaaaaaaaaaa")
    sc.save(s, p)
    assert sc.load(p) == s
    assert json.loads(p.read_text())["points"][0]["timestamp_s"] == 60


def test_short_title():
    s = make_script()
    assert sc.short_title(s, "Systems design", "Orig") == (
        '[Systems design] CPU caches in five minutes — TLDW of "Orig"'
    )
    assert sc.short_title(s, None, "Orig").startswith("CPU caches")
