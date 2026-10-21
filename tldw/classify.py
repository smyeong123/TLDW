"""Topic filter: one Claude call per video -> {topic | null, confidence, reason}.

Only used when config.topics is non-empty. The prompt sees metadata only (title, channel,
description, tags, chapters), never the transcript, so it is cheap and runs before Phase 2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

import anthropic
from pydantic import BaseModel, Field

from tldw.usage import usage

DESCRIPTION_CHARS = 2000  # long descriptions are mostly links/sponsors; the top is enough


@dataclass(frozen=True)
class VideoInfo:
    video_id: str
    title: str
    channel: str | None = None
    description: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Classification:
    topic: str | None
    confidence: float
    reason: str


class Classifier(Protocol):
    def __call__(self, video: VideoInfo) -> Classification: ...


class _ClassifierOutput(BaseModel):
    topic: str | None = Field(description="Exactly one of the allowed topics, or null")
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(description="One short sentence")


_CHAPTER_RE = re.compile(r"^\s*\(?(\d{1,2}:)?\d{1,2}:\d{2}\)?\s*[-–—:|]?\s*(.+)$")


def parse_chapters(description: str) -> list[str]:
    """Chapter titles from 'MM:SS Title' / 'H:MM:SS - Title' lines in a description."""
    out = []
    for line in description.splitlines():
        m = _CHAPTER_RE.match(line)
        if m:
            out.append(m.group(2).strip())
    return out


SYSTEM_PROMPT = """You sort YouTube videos into a user's topics of interest.
Pick the single best topic from the allowed list, or null if none fits.
Judge by what the video is actually about, not by passing mentions.
confidence is your probability (0-1) that the chosen topic is right;
use a low value when the metadata is thin or ambiguous."""


def build_prompt(video: VideoInfo, topics: list[str]) -> str:
    chapters = parse_chapters(video.description)
    parts = [
        "Allowed topics:",
        *[f"- {t}" for t in topics],
        "",
        f"Title: {video.title}",
        f"Channel: {video.channel or 'unknown'}",
    ]
    if video.tags:
        parts.append(f"Tags: {', '.join(video.tags[:30])}")
    if chapters:
        parts.append("Chapters: " + " | ".join(chapters[:40]))
    if video.description:
        parts += ["Description:", video.description[:DESCRIPTION_CHARS]]
    return "\n".join(parts)


class ClaudeClassifier:
    def __init__(self, client: anthropic.Anthropic, model: str, topics: list[str]):
        if not topics:
            raise ValueError("ClaudeClassifier needs at least one topic")
        self.client = client
        self.model = model
        self.topics = topics

    def __call__(self, video: VideoInfo) -> Classification:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": build_prompt(video, self.topics)}],
            output_format=_ClassifierOutput,
        )
        usage.add_llm(getattr(response, "usage", None))
        if response.stop_reason == "refusal" or response.parsed_output is None:
            return Classification(None, 0.0, f"no classification (stop: {response.stop_reason})")
        out = response.parsed_output
        return normalize(out.topic, out.confidence, out.reason, self.topics)


def normalize(
    topic: str | None, confidence: float, reason: str, topics: list[str]
) -> Classification:
    """Map the model's topic onto the configured spelling; unknown topics become null."""
    if topic is not None:
        by_key = {t.casefold().strip(): t for t in topics}
        topic = by_key.get(topic.casefold().strip())
        if topic is None:
            confidence = 0.0
    return Classification(topic, max(0.0, min(1.0, confidence)), reason)
