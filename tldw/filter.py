"""Decide, per tracked video, whether it gets summarized: watched? too long? on topic?

Order matters and is cheapest-first: watched % and length are free, classification costs
an LLM call, so it only runs for videos that survive the first two checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import anthropic

from tldw.classify import Classifier, VideoInfo
from tldw.config import Config
from tldw.state import Stage, State, VideoRecord

Verdict = Literal["process", "watched", "too_long", "off_topic", "error"]

_STAGE_FOR: dict[Verdict, Stage] = {
    "process": Stage.FILTERED_IN,
    "watched": Stage.WATCHED,
    "too_long": Stage.FILTERED_OUT,
    "off_topic": Stage.FILTERED_OUT,
}


@dataclass(frozen=True)
class Decision:
    video_id: str
    title: str
    verdict: Verdict
    reason: str
    topic: str | None = None
    confidence: float | None = None


def is_watched(rec: VideoRecord, cfg: Config) -> bool:
    return rec.watched_pct is not None and rec.watched_pct >= cfg.watched_threshold


def decide(
    rec: VideoRecord,
    cfg: Config,
    classifier: Classifier | None,
    info: VideoInfo | None = None,
) -> Decision:
    if is_watched(rec, cfg):
        return Decision(
            rec.id,
            rec.title,
            "watched",
            f"watched {rec.watched_pct:.0%} (threshold {cfg.watched_threshold:.0%})",
        )
    if rec.duration_s and rec.duration_s > cfg.max_source_minutes * 60:
        return Decision(
            rec.id,
            rec.title,
            "too_long",
            f"{rec.duration_s // 60} min > max_source_minutes {cfg.max_source_minutes}",
        )
    if not cfg.topics:
        return Decision(rec.id, rec.title, "process", "no topic filter")
    if classifier is None:
        raise ValueError("topics are configured but no classifier was given")
    c = classifier(info or VideoInfo(rec.id, rec.title, rec.channel))
    if c.topic is None or c.confidence < cfg.confidence_threshold:
        why = f"off topic ({c.topic or 'none'} @ {c.confidence:.2f}): {c.reason}"
        return Decision(rec.id, rec.title, "off_topic", why, c.topic, c.confidence)
    return Decision(rec.id, rec.title, "process", c.reason, c.topic, c.confidence)


def pending(state: State, cfg: Config) -> list[VideoRecord]:
    """`new` videos, plus `filtered_in` ones the user has since watched past the threshold."""
    later_watched = [r for r in state.list(Stage.FILTERED_IN) if is_watched(r, cfg)]
    return state.list(Stage.NEW) + later_watched


def run_filter(
    state: State,
    cfg: Config,
    classifier: Classifier | None,
    *,
    infos: dict[str, VideoInfo] | None = None,
    dry_run: bool = False,
) -> list[Decision]:
    """Decide every pending video; unless dry_run, move each to its stage and record why."""
    infos = infos or {}
    decisions = []
    for rec in pending(state, cfg):
        try:
            d = decide(rec, cfg, classifier, infos.get(rec.id))
        except anthropic.APIError as e:
            d = Decision(rec.id, rec.title, "error", f"classifier error: {e}")
            if not dry_run:
                state.fail(rec.id, d.reason)
            decisions.append(d)
            continue
        if not dry_run:
            state.set_stage(rec.id, _STAGE_FOR[d.verdict], topic=d.topic)
            state.set_note(rec.id, d.reason)
        decisions.append(d)
    return decisions
