"""Snippet mode: a page per video that plays its key segments through YouTube's embed player.

Nothing is downloaded (plans.md hard constraint): the page loads the official IFrame
player and asks it for [start, end) of the source for each point, one after another,
with the point's claim and detail under the player. Pages live in out/snippets/:
  <id>.html  the player page
  <id>.json  title/topic/channel for the index
  index.html every summary, newest first
YouTube's embed refuses to play on file:// pages, so they are opened through `tldw serve`
(http://127.0.0.1:<port>/).
"""

from __future__ import annotations

import html
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from tldw.script import Script, clip_windows, fmt_ts


@dataclass(frozen=True)
class PageMeta:
    video_id: str
    title: str  # summary title from the script
    original_title: str
    channel: str | None
    topic: str | None
    language: str
    created_at: str


def pages_dir(out_dir: Path) -> Path:
    return out_dir / "snippets"


def page_path(out_dir: Path, video_id: str) -> Path:
    return pages_dir(out_dir) / f"{video_id}.html"


def watch_url(video_id: str, start_s: int = 0) -> str:
    return f"https://youtu.be/{video_id}" + (f"?t={start_s}" if start_s else "")


def _json_for_script_tag(data: object) -> str:
    """JSON that is safe inside <script>: no '</' can close the tag early."""
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


_STYLE = """
:root { --bg:#0f172a; --card:#1e293b; --text:#f8fafc; --muted:#94a3b8; --accent:#f59e0b; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text);
  font:16px/1.5 -apple-system, "Apple SD Gothic Neo", "Noto Sans CJK KR", system-ui, sans-serif; }
main { max-width:560px; margin:0 auto; padding:16px; }
a { color:var(--accent); }
.pill { display:inline-block; background:var(--accent); color:#0f172a; font-weight:700;
  font-size:13px; padding:2px 10px; border-radius:999px; }
h1 { font-size:22px; line-height:1.3; margin:10px 0 4px; }
.src { color:var(--muted); font-size:14px; margin:0 0 12px; }
.context { margin:0 0 16px; }
.player { position:relative; width:100%; aspect-ratio:16/9; background:#000; border-radius:12px;
  overflow:hidden; }
.player > * { position:absolute; inset:0; width:100%; height:100%; }
.card { background:var(--card); border-radius:12px; padding:16px; margin-top:12px; }
.meta { display:flex; justify-content:space-between; color:var(--muted); font-size:14px; }
.ts { color:var(--accent); font-variant-numeric:tabular-nums; }
.claim { font-size:20px; font-weight:700; line-height:1.35; margin:8px 0; }
.detail { color:var(--muted); margin:0; }
.nav { display:flex; gap:8px; margin-top:12px; }
button { flex:1; font:inherit; font-weight:600; padding:10px; border:0; border-radius:10px;
  background:var(--card); color:var(--text); cursor:pointer; }
button.primary { background:var(--accent); color:#0f172a; }
button:disabled { opacity:.4; cursor:default; }
.takeaway { border-left:4px solid var(--accent); padding:8px 12px; margin-top:20px; }
.error { color:#fca5a5; }
ul.list { list-style:none; padding:0; }
ul.list li { background:var(--card); border-radius:12px; padding:12px 16px; margin:8px 0; }
ul.list a { color:var(--text); text-decoration:none; font-weight:700; }
"""


def render_page(script: Script, meta: PageMeta, duration_s: int | None) -> str:
    clips = [
        {
            "start": start,
            "end": end,
            "label": f"{fmt_ts(start)}–{fmt_ts(end)}",
            "claim": p.claim,
            "detail": p.detail,
        }
        for p, (start, end) in zip(script.points, clip_windows(script, duration_s), strict=True)
    ]
    data = {"videoId": meta.video_id, "clips": clips, "watch": watch_url(meta.video_id)}
    e = html.escape
    topic = f'<span class="pill">{e(meta.topic)}</span>' if meta.topic else ""
    src = f"TLDW of “{e(meta.original_title)}”" + (f" · {e(meta.channel)}" if meta.channel else "")
    return f"""<!doctype html>
<html lang="{e(meta.language)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(script.title)} — TLDW</title>
<style>{_STYLE}</style>
</head>
<body>
<main>
  <p><a href="/">← All summaries</a></p>
  {topic}
  <h1>{e(script.title)}</h1>
  <p class="src">{src}</p>
  <p class="context">{e(script.context)}</p>
  <div class="player"><div id="player"></div></div>
  <div class="card">
    <div class="meta"><span id="pos"></span><span class="ts" id="ts"></span></div>
    <p class="claim" id="claim"></p>
    <p class="detail" id="detail"></p>
    <p class="error" id="err" hidden></p>
  </div>
  <div class="nav">
    <button id="prev">Prev</button>
    <button id="play" class="primary">Play</button>
    <button id="next">Next</button>
  </div>
  <p class="takeaway">{e(script.takeaway)}</p>
  <p class="src"><a id="open" href="{e(data["watch"])}" target="_blank" rel="noopener">
    Open on YouTube</a></p>
</main>
<script>const TLDW = {_json_for_script_tag(data)};</script>
<script>
let player, i = 0, started = false;
const $ = (id) => document.getElementById(id);
function show() {{
  const c = TLDW.clips[i];
  $("pos").textContent = (i + 1) + " / " + TLDW.clips.length;
  $("ts").textContent = "▶ " + c.label;
  $("claim").textContent = c.claim;
  $("detail").textContent = c.detail;
  $("open").href = TLDW.watch + "?t=" + c.start;
  $("prev").disabled = i === 0;
  $("next").disabled = i === TLDW.clips.length - 1;
}}
function play(n) {{
  i = n; started = true; show(); $("err").hidden = true;
  const c = TLDW.clips[i];
  player.loadVideoById({{videoId: TLDW.videoId, startSeconds: c.start, endSeconds: c.end}});
}}
function onYouTubeIframeAPIReady() {{
  const c = TLDW.clips[0];
  player = new YT.Player("player", {{
    videoId: TLDW.videoId,
    playerVars: {{start: c.start, end: c.end, playsinline: 1, rel: 0,
      origin: window.location.origin}},
    events: {{
      onStateChange: (ev) => {{
        if (ev.data === YT.PlayerState.ENDED && started && i < TLDW.clips.length - 1) play(i + 1);
      }},
      onError: () => {{
        $("err").textContent = "This video can't be played here (embedding off or removed). " +
          "Use “Open on YouTube”.";
        $("err").hidden = false;
      }},
    }},
  }});
}}
$("play").onclick = () => play(i);
$("prev").onclick = () => play(Math.max(0, i - 1));
$("next").onclick = () => play(Math.min(TLDW.clips.length - 1, i + 1));
show();
</script>
<script src="https://www.youtube.com/iframe_api"></script>
</body>
</html>
"""


def render_index(metas: list[PageMeta]) -> str:
    e = html.escape
    items = []
    for m in sorted(metas, key=lambda m: m.created_at, reverse=True):
        topic = f'<span class="pill">{e(m.topic)}</span> ' if m.topic else ""
        items.append(
            f'<li>{topic}<a href="/s/{e(m.video_id)}">{e(m.title)}</a>'
            f'<div class="src">{e(m.original_title)}'
            + (f" · {e(m.channel)}" if m.channel else "")
            + "</div></li>"
        )
    body = "\n".join(items) or '<li class="src">No summaries yet. Run `tldw run`.</li>'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TLDW summaries</title>
<style>{_STYLE}</style>
</head>
<body>
<main>
  <h1>TLDW summaries</h1>
  <ul class="list">
{body}
  </ul>
</main>
</body>
</html>
"""


def write_page(out_dir: Path, script: Script, meta: PageMeta, duration_s: int | None) -> Path:
    """Write <id>.html + <id>.json and refresh index.html. Returns the page path."""
    d = pages_dir(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = page_path(out_dir, meta.video_id)
    path.write_text(render_page(script, meta, duration_s), encoding="utf-8")
    (d / f"{meta.video_id}.json").write_text(
        json.dumps(asdict(meta), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_index(out_dir)
    return path


def write_index(out_dir: Path) -> Path:
    d = pages_dir(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    metas = []
    for f in sorted(d.glob("*.json")):
        try:
            metas.append(PageMeta(**json.loads(f.read_text(encoding="utf-8"))))
        except (ValueError, TypeError):
            continue  # a broken sidecar shouldn't hide every other summary
    path = d / "index.html"
    path.write_text(render_index(metas), encoding="utf-8")
    return path


def make_meta(
    script: Script,
    *,
    video_id: str,
    original_title: str,
    channel: str | None,
    topic: str | None,
    now: datetime | None = None,
) -> PageMeta:
    return PageMeta(
        video_id=video_id,
        title=script.title,
        original_title=original_title,
        channel=channel,
        topic=topic,
        language=script.language,
        created_at=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
    )


def telegram_text(script: Script, title: str, video_id: str, duration_s: int | None) -> str:
    """Plain-text Telegram message: each point links to its segment (Telegram auto-links)."""
    lines = [title, "", script.context, ""]
    for n, (p, (start, end)) in enumerate(
        zip(script.points, clip_windows(script, duration_s), strict=True), 1
    ):
        lines += [
            f"{n}. {p.claim}",
            f"▶ {fmt_ts(start)}–{fmt_ts(end)} {watch_url(video_id, start)}",
        ]
    lines += ["", script.takeaway, "", watch_url(video_id)]
    return "\n".join(lines)
