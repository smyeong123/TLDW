import pytest

from tldw import tts
from tldw.script import Point, Script
from tldw.tts import Audio, EdgeTTS, Word


class FakeCommunicate:
    instances = []

    def __init__(self, text, voice, boundary):
        self.text, self.voice, self.boundary = text, voice, boundary
        FakeCommunicate.instances.append(self)

    def stream_sync(self):
        yield {"type": "audio", "data": b"ID3fake"}
        yield {"type": "WordBoundary", "offset": 1_000_000, "duration": 5_000_000, "text": "Hi"}
        yield {"type": "audio", "data": b"more"}
        yield {"type": "WordBoundary", "offset": 7_000_000, "duration": 3_000_000, "text": "there"}


def test_edge_tts_writes_audio_and_word_timings(tmp_path):
    engine = EdgeTTS(communicate=FakeCommunicate, probe=lambda p: None)
    out = engine.synthesize("Hi there", "en-US-AriaNeural", tmp_path / "a" / "x.mp3")
    assert out.path.read_bytes() == b"ID3fakemore"
    assert out.words == [Word(0.1, 0.6, "Hi"), Word(0.7, 1.0, "there")]
    assert out.duration_s == 1.0  # no ffprobe -> last word end
    assert FakeCommunicate.instances[-1].boundary == "WordBoundary"


def test_edge_tts_prefers_probe_duration(tmp_path):
    engine = EdgeTTS(communicate=FakeCommunicate, probe=lambda p: 1.42)
    assert engine.synthesize("x", "v", tmp_path / "x.mp3").duration_s == 1.42


def test_edge_tts_empty_audio_raises(tmp_path):
    class Silent(FakeCommunicate):
        def stream_sync(self):
            return iter(())

    with pytest.raises(RuntimeError, match="no audio"):
        EdgeTTS(communicate=Silent, probe=lambda p: None).synthesize("x", "v", tmp_path / "x.mp3")


def test_synthesize_script_one_file_per_section(tmp_path):
    script = Script(
        title="T",
        language="en",
        context="Context.",
        points=[Point(claim=f"C{i}.", detail=f"D{i}.", timestamp_s=i) for i in range(3)],
        takeaway="Take.",
    )
    calls = []

    class Recorder:
        def synthesize(self, text, voice, out_path):
            calls.append((text, out_path.name))
            return Audio(out_path, 1.0)

    audios = tts.synthesize_script(script, "v", tmp_path, Recorder())
    assert len(audios) == 5
    assert calls == [
        ("Context.", "00_context.mp3"),
        ("C0. D0.", "01_point.mp3"),
        ("C1. D1.", "02_point.mp3"),
        ("C2. D2.", "03_point.mp3"),
        ("Take.", "04_takeaway.mp3"),
    ]
