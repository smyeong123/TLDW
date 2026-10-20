"""Slides (Pillow) for the 9:16 short. DRAFT STYLE, waiting for the user's approval.

plans.md: before Phase 3 rendering is built out, the user approves one sample slide.
Only the point-slide layout exists so far (`tldw sample-slide`). Title/takeaway slides,
captions, and ffmpeg assembly come after approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
# Shorts overlays its own UI: keep text out of the bottom ~22% and the right-hand buttons.
SAFE_LEFT, SAFE_RIGHT, SAFE_TOP, SAFE_BOTTOM = 80, W - 160, 140, H - 420

FONT_CANDIDATES = [
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",  # macOS, Latin + Hangul
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",  # Debian/Ubuntu fonts-noto-cjk
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
]


@dataclass(frozen=True)
class SlideStyle:
    background: str = "#0F172A"  # slate-900
    text: str = "#F8FAFC"
    muted: str = "#CBD5E1"
    accent: str = "#F59E0B"  # amber label
    accent_text: str = "#111827"
    claim_size: int = 76
    detail_size: int = 50
    label_size: int = 38
    meta_size: int = 36


def find_font(candidates: list[str] = FONT_CANDIDATES) -> str:
    for c in candidates:
        if Path(c).exists():
            return c
    raise FileNotFoundError("no CJK-capable font found; install Noto Sans CJK or set a font path")


def _font(path: str, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    # AppleSDGothicNeo.ttc: index 0 regular, 6 bold (other .ttc files: fall back to 0)
    if bold and path.endswith("AppleSDGothicNeo.ttc"):
        return ImageFont.truetype(path, size, index=6)
    return ImageFont.truetype(path, size)


def wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Greedy wrap by words; words wider than a line (e.g. long CJK runs) break by char."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if font.getlength(candidate) <= max_width:
            line = candidate
            continue
        if line:
            lines.append(line)
        line = ""
        for ch in word:
            if font.getlength(line + ch) > max_width and line:
                lines.append(line)
                line = ""
            line += ch
    if line:
        lines.append(line)
    return lines


def _draw_block(draw, xy, text, font, fill, max_width, line_gap) -> int:
    x, y = xy
    for line in wrap(text, font, max_width):
        draw.text((x, y), line, font=font, fill=fill)
        y += int(font.size * line_gap)
    return y


def render_point_slide(
    out_path: Path,
    *,
    claim: str,
    detail: str,
    topic: str | None,
    index: int,
    total: int,
    timestamp: str,
    channel: str | None,
    style: SlideStyle | None = None,
    font_path: str | None = None,
) -> Path:
    style = style or SlideStyle()
    font_path = font_path or find_font()
    img = Image.new("RGB", (W, H), style.background)
    d = ImageDraw.Draw(img)
    width = SAFE_RIGHT - SAFE_LEFT

    # top-left: topic pill + "2 / 4"
    label_font = _font(font_path, style.label_size, bold=True)
    x = SAFE_LEFT
    if topic:
        pad = 18
        tw = label_font.getlength(topic)
        d.rounded_rectangle(
            (x, SAFE_TOP, x + tw + 2 * pad, SAFE_TOP + style.label_size + 2 * pad),
            radius=16,
            fill=style.accent,
        )
        d.text((x + pad, SAFE_TOP + pad - 4), topic, font=label_font, fill=style.accent_text)
        x += tw + 2 * pad + 24
    d.text((x, SAFE_TOP + 14), f"{index} / {total}", font=label_font, fill=style.muted)

    # middle: claim (big, bold) then detail
    claim_font = _font(font_path, style.claim_size, bold=True)
    detail_font = _font(font_path, style.detail_size)
    y = SAFE_TOP + 260
    y = _draw_block(d, (SAFE_LEFT, y), claim, claim_font, style.text, width, 1.3)
    d.line((SAFE_LEFT, y + 30, SAFE_LEFT + 120, y + 30), fill=style.accent, width=8)
    _draw_block(d, (SAFE_LEFT, y + 80), detail, detail_font, style.muted, width, 1.4)

    # bottom (above the Shorts UI): source on the left, timestamp on the right
    meta_font = _font(font_path, style.meta_size)
    d.text(
        (SAFE_LEFT, SAFE_BOTTOM),
        f"TLDW · {channel or ''}".rstrip(" ·"),
        font=meta_font,
        fill=style.muted,
    )
    ts = f"▶ {timestamp}"
    d.text(
        (SAFE_RIGHT - meta_font.getlength(ts), SAFE_BOTTOM), ts, font=meta_font, fill=style.accent
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path
