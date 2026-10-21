"""Summary script: one Claude call over the transcript -> Script (strict JSON), validated.

Spec (plans.md "Output spec"): context (<=2 sentences), 3-5 points each with one claim, one
supporting detail and a source timestamp, one-sentence takeaway; 60-150 s when read aloud.
If validation fails, the model gets one retry with the list of problems.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field

from tldw.transcript import Transcript
from tldw.usage import usage

MIN_SECONDS, MAX_SECONDS = 60, 150
CHUNK_SECONDS = 30  # transcript lines are merged into ~30 s blocks to save tokens
# Speaking rates for the length estimate: words/s for spaced languages, chars/s for CJK.
WORDS_PER_SECOND = 2.5  # 150 wpm
CJK_CHARS_PER_SECOND = {"ko": 6.5, "ja": 7.0, "zh": 4.5}


class Point(BaseModel):
    claim: str = Field(description="One key claim from the video, one sentence")
    detail: str = Field(description="One supporting detail, example, or number")
    timestamp_s: int = Field(description="Second in the source video where this is discussed")


class Script(BaseModel):
    title: str = Field(description="Short title for the summary, max ~8 words")
    language: str = Field(description="Language code the script is written in, e.g. en, ko")
    context: str = Field(description="At most 2 sentences: what the video is, who made it, why")
    points: list[Point]
    takeaway: str = Field(description="One sentence: what to remember or do")

    def narration(self) -> list[str]:
        """The text the voice reads, section by section (title slide reads the context)."""
        return [self.context, *(f"{p.claim} {p.detail}" for p in self.points), self.takeaway]


class ScriptError(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoMeta:
    video_id: str
    title: str
    channel: str | None
    duration_s: int | None


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def transcript_text(t: Transcript, chunk_s: int = CHUNK_SECONDS) -> str:
    """'[m:ss] text' blocks of ~chunk_s seconds each."""
    lines: list[str] = []
    block: list[str] = []
    block_start = None
    for seg in t.segments:
        if block_start is None:
            block_start = seg.start
        elif seg.start - block_start >= chunk_s:
            lines.append(f"[{fmt_ts(block_start)}] {' '.join(block)}")
            block, block_start = [], seg.start
        block.append(seg.text)
    if block:
        lines.append(f"[{fmt_ts(block_start or 0)}] {' '.join(block)}")
    return "\n".join(lines)


def estimate_seconds(text: str, language: str) -> float:
    lang = language.split("-")[0].lower()
    if lang in CJK_CHARS_PER_SECOND:
        chars = len(re.sub(r"\s+", "", text))
        return chars / CJK_CHARS_PER_SECOND[lang]
    return len(text.split()) / WORDS_PER_SECOND


_SENTENCE_END = re.compile(r"[.!?。！？](?=\s|$)")


def validate(script: Script, *, duration_s: float, language: str) -> list[str]:
    """Problems with the script; empty list = valid."""
    problems = []
    if not 3 <= len(script.points) <= 5:
        problems.append(f"need 3-5 points, got {len(script.points)}")
    for i, p in enumerate(script.points, 1):
        if not 0 <= p.timestamp_s <= duration_s:
            problems.append(
                f"point {i} timestamp {p.timestamp_s}s is outside the video (0-{int(duration_s)}s)"
            )
    if len(_SENTENCE_END.findall(script.context.strip())) > 2:
        problems.append("context must be at most 2 sentences")
    if len(_SENTENCE_END.findall(script.takeaway.strip())) > 1:
        problems.append("takeaway must be exactly 1 sentence")
    if script.language.split("-")[0].lower() != language.split("-")[0].lower():
        problems.append(f"script must be written in '{language}', got '{script.language}'")
    secs = estimate_seconds(" ".join(script.narration()), language)
    if not MIN_SECONDS <= secs <= MAX_SECONDS:
        problems.append(
            f"read-aloud length is ~{secs:.0f}s; it must be {MIN_SECONDS}-{MAX_SECONDS}s "
            f"({'shorten' if secs > MAX_SECONDS else 'lengthen'} the points)"
        )
    return problems


SYSTEM_PROMPT = f"""You write the narration for a short vertical summary video (a "TLDW")
of a YouTube video the viewer saved but has no time to watch.

Write for the ear: plain spoken sentences, no markdown, no lists, no emoji.
Structure, exactly:
1. context: at most 2 sentences. What the video is, who made it, why it matters.
2. points: 3 to 5 key points from the video, in the order they appear. Each has one
   claim (one sentence) and one supporting detail (an example, number, or reason from
   the video), plus timestamp_s: the second in the source where it is discussed,
   taken from the [m:ss] markers.
3. takeaway: exactly 1 sentence: what to remember or do.
Total read-aloud length: {MIN_SECONDS}-{MAX_SECONDS} seconds (about 150 words per minute).
Only state what the video says; do not add outside facts. If the video is not
informational (e.g. vlog, music, story), summarize what happens and its main message."""


def build_prompt(meta: VideoMeta, t: Transcript, language: str) -> str:
    return "\n".join(
        [
            f"Write the script in language: {language}",
            f"Video title: {meta.title}",
            f"Channel: {meta.channel or 'unknown'}",
            f"Length: {fmt_ts(meta.duration_s or t.end_s)}",
            "",
            "Transcript:",
            transcript_text(t),
        ]
    )


def target_language(output_language: str, t: Transcript) -> str:
    return t.language if output_language == "match" else output_language


def write_script(
    client: anthropic.Anthropic,
    model: str,
    meta: VideoMeta,
    t: Transcript,
    *,
    output_language: str = "match",
) -> Script:
    """Generate + validate, retrying once with feedback. Raises ScriptError if still invalid."""
    language = target_language(output_language, t)
    duration = float(meta.duration_s or t.end_s)
    messages: list[dict] = [{"role": "user", "content": build_prompt(meta, t, language)}]
    problems: list[str] = []
    for attempt in range(2):
        response = client.messages.parse(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            output_config={"effort": "medium"},
            messages=messages,
            output_format=Script,
        )
        usage.add_llm(getattr(response, "usage", None))
        script = response.parsed_output
        if response.stop_reason == "refusal" or script is None:
            raise ScriptError(f"{meta.video_id}: no script (stop: {response.stop_reason})")
        problems = validate(script, duration_s=duration, language=language)
        if not problems:
            return script
        if attempt == 0:
            messages += [
                {"role": "assistant", "content": script.model_dump_json()},
                {
                    "role": "user",
                    "content": "Fix these problems and return the full script again:\n- "
                    + "\n- ".join(problems),
                },
            ]
    raise ScriptError(f"{meta.video_id}: invalid script after retry: {'; '.join(problems)}")


def script_path(cache_dir: Path, video_id: str) -> Path:
    return cache_dir / f"{video_id}.script.json"


def save(script: Script, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script.model_dump_json(indent=2), encoding="utf-8")


def load(path: Path) -> Script:
    return Script.model_validate_json(path.read_text(encoding="utf-8"))


def short_title(script: Script, topic: str | None, original_title: str) -> str:
    """plans.md title format: `[Topic] <short title> — TLDW of "<original title>"`."""
    prefix = f"[{topic}] " if topic else ""
    return f'{prefix}{script.title} — TLDW of "{original_title}"'
