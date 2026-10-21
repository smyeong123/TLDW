import logging
from types import SimpleNamespace

from tldw.usage import Usage, report, setup_logging


def test_usage_counts_llm_and_tts():
    u = Usage()
    u.add_llm(SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=50))
    u.add_llm(None)
    u.add_tts(61.4)
    assert (u.llm_calls, u.llm_input_tokens, u.llm_output_tokens) == (2, 150, 20)
    assert report(1650, u) == (
        "YouTube API: 1650 units · Claude: 2 calls, 150 in / 20 out tokens · TTS: 61s"
    )


def test_setup_logging_writes_and_is_idempotent(tmp_path):
    path = setup_logging(tmp_path / "logs")
    setup_logging(tmp_path / "logs")
    root = logging.getLogger()
    mine = [h for h in root.handlers if getattr(h, "baseFilename", "") == str(path.resolve())]
    assert len(mine) == 1
    logging.getLogger("tldw.test").info("hello log")
    mine[0].flush()
    assert "hello log" in path.read_text()
    root.removeHandler(mine[0])
    mine[0].close()
