"""Configuration: non-secret settings from config.yaml, secrets from .env / environment."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Delivery = Literal["youtube", "telegram"]


class Secrets(BaseSettings):
    """Values that must never be committed. Read from .env or the environment."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    google_client_secret_file: Path = Path("client_secret.json")
    google_token_file: Path = Path("token.json")


class Config(BaseModel):
    """Contents of config.yaml."""

    playlist_name: str = "TLDW"
    output_playlist_name: str = "TLDW Shorts"
    topics: list[str] = Field(min_length=1)
    output_language: str = "match"
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    max_source_minutes: int = Field(default=120, gt=0)
    max_shorts_per_run: int = Field(default=5, gt=0)
    delivery: list[Delivery] = Field(default_factory=lambda: ["youtube"])
    tts_voice: dict[str, str] = Field(default_factory=lambda: {"en": "en-US-AriaNeural"})
    whisper_fallback: bool = False
    state_db: Path = Path("state.db")
    cache_dir: Path = Path("cache")
    out_dir: Path = Path("out")
    logs_dir: Path = Path("logs")

    @field_validator("output_language")
    @classmethod
    def _check_language(cls, v: str) -> str:
        v = v.strip().lower()
        if v != "match" and not (2 <= len(v) <= 5):
            raise ValueError("output_language must be 'match' or a language code like 'ko'/'en'")
        return v

    @field_validator("delivery")
    @classmethod
    def _dedupe_delivery(cls, v: list[Delivery]) -> list[Delivery]:
        if not v:
            raise ValueError("delivery must list at least one of: youtube, telegram")
        return list(dict.fromkeys(v))

    def voice_for(self, language: str) -> str:
        """TTS voice for a language code; falls back to the first configured voice."""
        lang = language.lower().split("-")[0]
        if lang in self.tts_voice:
            return self.tts_voice[lang]
        return next(iter(self.tts_voice.values()))


class AppConfig(BaseModel):
    config: Config
    secrets: Secrets
    root: Path

    def resolve(self, p: Path) -> Path:
        return p if p.is_absolute() else self.root / p

    @property
    def state_db(self) -> Path:
        return self.resolve(self.config.state_db)

    @property
    def cache_dir(self) -> Path:
        return self.resolve(self.config.cache_dir)

    @property
    def out_dir(self) -> Path:
        return self.resolve(self.config.out_dir)

    @property
    def logs_dir(self) -> Path:
        return self.resolve(self.config.logs_dir)


def load_config(path: Path | str = "config.yaml") -> AppConfig:
    """Load config.yaml (relative to cwd) and secrets from .env / environment."""
    yaml_path = Path(path)
    if not yaml_path.exists():
        raise FileNotFoundError(f"config file not found: {yaml_path}")
    with yaml_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    root = yaml_path.resolve().parent
    return AppConfig(config=Config(**raw), secrets=Secrets(), root=root)
