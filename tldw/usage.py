"""Per-run usage meters for the end-of-run report (YouTube quota lives in youtube.quota).

Like youtube.quota, `usage` is a module-level meter: one CLI invocation = one run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


@dataclass
class Usage:
    llm_calls: int = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    tts_seconds: float = 0.0

    def add_llm(self, response_usage: Any) -> None:
        """Record an Anthropic `response.usage` (cache reads/writes count as input)."""
        self.llm_calls += 1
        if response_usage is None:
            return
        for attr in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            self.llm_input_tokens += getattr(response_usage, attr, 0) or 0
        self.llm_output_tokens += getattr(response_usage, "output_tokens", 0) or 0

    def add_tts(self, seconds: float) -> None:
        self.tts_seconds += seconds

    def reset(self) -> None:
        self.__init__()


usage = Usage()


def report(quota_units: int, u: Usage = usage) -> str:
    return (
        f"YouTube API: {quota_units} units · "
        f"Claude: {u.llm_calls} calls, {u.llm_input_tokens:,} in / {u.llm_output_tokens:,} out "
        f"tokens · TTS: {u.tts_seconds:.0f}s"
    )


def setup_logging(logs_dir: Path, *, level: int = logging.INFO) -> Path:
    """logs/tldw.log, rotated at 1 MB, 5 old files kept. Returns the log path."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "tldw.log"
    root = logging.getLogger()
    if not any(
        isinstance(h, RotatingFileHandler) and h.baseFilename == str(path.resolve())
        for h in root.handlers
    ):
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    root.setLevel(level)
    return path
