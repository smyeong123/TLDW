from pathlib import Path

import pytest

from tldw.config import Config, load_config

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_repo_config_yaml_loads():
    cfg = load_config(REPO_ROOT / "config.yaml")
    assert cfg.config.playlist_name == "TLDW"
    assert cfg.config.output_language == "match"
    assert set(cfg.config.delivery) == {"youtube", "telegram"}
    assert cfg.config.whisper_fallback is False
    assert cfg.state_db == REPO_ROOT / "state.db"


def test_topics_required():
    with pytest.raises(ValueError):
        Config(topics=[])


def test_output_language_validation():
    assert Config(topics=["x"], output_language="KO").output_language == "ko"
    with pytest.raises(ValueError):
        Config(topics=["x"], output_language="not-a-language-code")


def test_delivery_deduped_and_nonempty():
    assert Config(topics=["x"], delivery=["youtube", "youtube"]).delivery == ["youtube"]
    with pytest.raises(ValueError):
        Config(topics=["x"], delivery=[])


def test_voice_fallback():
    c = Config(topics=["x"], tts_voice={"ko": "ko-voice", "en": "en-voice"})
    assert c.voice_for("ko-KR") == "ko-voice"
    assert c.voice_for("ja") == "ko-voice"  # first configured voice
