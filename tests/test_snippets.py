import json
from datetime import UTC, datetime

from tldw import snippets as sn
from tldw.script import Point, Script

VID = "aaaaaaaaaaa"


def _script(title="Caches", claim="Order matters."):
    return Script(
        title=title,
        language="en",
        context="A talk.",
        points=[
            Point(claim=claim, detail="64-byte lines.", timestamp_s=60, end_s=90),
            Point(claim="B.", detail="d", timestamp_s=200),
            Point(claim="C.", detail="d", timestamp_s=400),
        ],
        takeaway="Keep data hot.",
    )


def _meta(script, **kw):
    base = dict(video_id=VID, original_title="Orig", channel="Chan", topic="ML / AI")
    base.update(kw)
    return sn.make_meta(script, now=datetime(2026, 11, 3, tzinfo=UTC), **base)


def _data(page: str) -> dict:
    raw = page.split("const TLDW = ", 1)[1].split(";</script>", 1)[0]
    return json.loads(raw)


def test_page_has_clips_and_embed_player():
    s = _script()
    page = sn.render_page(s, _meta(s), duration_s=420)
    data = _data(page)
    assert data["videoId"] == VID
    assert [(c["start"], c["end"]) for c in data["clips"]] == [(60, 90), (200, 230), (400, 420)]
    assert "https://www.youtube.com/iframe_api" in page
    assert "googlevideo" not in page  # only the official player, never stream URLs


def test_page_escapes_text():
    s = _script(title="<b>x</b>", claim="</script><script>alert(1)</script>")
    page = sn.render_page(s, _meta(s, original_title='"><img src=x>'), duration_s=None)
    assert "<b>x</b>" not in page and "&lt;b&gt;x&lt;/b&gt;" in page
    assert "<img src=x>" not in page
    assert "</script><script>alert(1)" not in page
    assert _data(page)["clips"][0]["claim"] == "</script><script>alert(1)</script>"


def test_write_page_and_index(tmp_path):
    s = _script()
    path = sn.write_page(tmp_path, s, _meta(s), 420)
    assert path == tmp_path / "snippets" / f"{VID}.html"
    index = (tmp_path / "snippets" / "index.html").read_text()
    assert f'href="/s/{VID}"' in index and "Caches" in index
    (tmp_path / "snippets" / "broken.json").write_text("{")
    assert f"/s/{VID}" in sn.write_index(tmp_path).read_text()  # broken sidecar ignored


def test_telegram_text_links_each_clip():
    text = sn.telegram_text(_script(), "[ML] Caches", VID, 420)
    assert f"▶ 1:00–1:30 https://youtu.be/{VID}?t=60" in text
    assert f"▶ 6:40–7:00 https://youtu.be/{VID}?t=400" in text
    assert text.endswith(f"https://youtu.be/{VID}")
