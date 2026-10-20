"""Text shared by every delivery channel: title and description built from the script."""

from __future__ import annotations

from tldw.script import Script, fmt_ts, short_title

YOUTUBE_TITLE_MAX = 100  # YouTube rejects longer titles
YOUTUBE_DESCRIPTION_MAX_BYTES = 5000
TELEGRAM_CAPTION_MAX = 1024


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def title(script: Script, topic: str | None, original_title: str) -> str:
    """plans.md format, shortened to fit YouTube's 100-char limit (original title clipped first)."""
    full = short_title(script, topic, original_title)
    if len(full) <= YOUTUBE_TITLE_MAX:
        return full
    overflow = len(full) - YOUTUBE_TITLE_MAX
    keep = max(10, len(original_title) - overflow - 1)
    full = short_title(script, topic, _clip(original_title, keep))
    return _clip(full, YOUTUBE_TITLE_MAX)


def description(script: Script, source_url: str, original_title: str, channel: str | None) -> str:
    lines = [
        script.takeaway,
        "",
        f'Summary of "{original_title}"' + (f" by {channel}" if channel else ""),
        source_url,
        "",
    ]
    for p in script.points:
        lines.append(f"{fmt_ts(p.timestamp_s)} {p.claim}")
    lines += ["", "Made with TLDW from the video's captions."]
    text = "\n".join(lines)
    while len(text.encode("utf-8")) > YOUTUBE_DESCRIPTION_MAX_BYTES:
        text = text[: int(len(text) * 0.9)]
    return text


def telegram_caption(script: Script, title_text: str, source_url: str) -> str:
    return _clip(f"{title_text}\n\n{script.takeaway}\n\n{source_url}", TELEGRAM_CAPTION_MAX)
