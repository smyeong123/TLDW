from types import SimpleNamespace

import pytest

from tldw import classify
from tldw.classify import Classification, ClaudeClassifier, VideoInfo

TOPICS = ["ML / AI", "Finance"]


def test_parse_chapters():
    desc = "Intro text\n0:00 Intro\n02:15 - Attention\n1:02:03 | Wrap up\nnot 12:00pm a chapter"
    assert classify.parse_chapters(desc) == ["Intro", "Attention", "Wrap up"]


def test_build_prompt_includes_metadata():
    v = VideoInfo("id", "Transformers explained", "Chan", "0:00 Intro\n3:00 Heads", ["ml", "nlp"])
    p = classify.build_prompt(v, TOPICS)
    assert "- ML / AI" in p and "- Finance" in p
    assert "Title: Transformers explained" in p
    assert "Tags: ml, nlp" in p
    assert "Chapters: Intro | Heads" in p


def test_normalize_matches_configured_spelling():
    assert classify.normalize("ml / ai", 0.9, "r", TOPICS) == Classification("ML / AI", 0.9, "r")
    assert classify.normalize("Cooking", 0.9, "r", TOPICS) == Classification(None, 0.0, "r")
    assert classify.normalize(None, 0.4, "r", TOPICS).topic is None


class _FakeMessages:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def _client(response):
    return SimpleNamespace(messages=_FakeMessages(response))


def test_claude_classifier_calls_parse():
    out = classify._ClassifierOutput(topic="Finance", confidence=0.8, reason="about ETFs")
    client = _client(SimpleNamespace(stop_reason="end_turn", parsed_output=out))
    c = ClaudeClassifier(client, "claude-opus-5-5", TOPICS)
    assert c(VideoInfo("id", "ETFs 101")) == Classification("Finance", 0.8, "about ETFs")
    kw = client.messages.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_format"] is classify._ClassifierOutput
    assert "ETFs 101" in kw["messages"][0]["content"]


def test_claude_classifier_refusal_is_null():
    client = _client(SimpleNamespace(stop_reason="refusal", parsed_output=None))
    got = ClaudeClassifier(client, "m", TOPICS)(VideoInfo("id", "t"))
    assert got.topic is None and got.confidence == 0.0


def test_claude_classifier_needs_topics():
    with pytest.raises(ValueError):
        ClaudeClassifier(_client(None), "m", [])
