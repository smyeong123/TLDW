import pytest
from PIL import Image

from tldw import render


@pytest.fixture
def font_path():
    try:
        return render.find_font()
    except FileNotFoundError:
        pytest.skip("no CJK font on this machine")


def test_wrap_respects_width(font_path):
    font = render._font(font_path, 50)
    lines = render.wrap("one two three four five six seven eight nine ten", font, 300)
    assert len(lines) > 1
    assert all(font.getlength(line) <= 300 for line in lines)


def test_wrap_breaks_long_cjk_run(font_path):
    font = render._font(font_path, 50)
    lines = render.wrap("가" * 40, font, 400)
    assert len(lines) > 1 and "".join(lines) == "가" * 40


def test_render_point_slide_size(tmp_path, font_path):
    out = render.render_point_slide(
        tmp_path / "s.png",
        claim="Claim.",
        detail="Detail.",
        topic="ML / AI",
        index=1,
        total=3,
        timestamp="1:23",
        channel="Chan",
        font_path=font_path,
    )
    with Image.open(out) as img:
        assert img.size == (1080, 1920)


def test_find_font_missing():
    with pytest.raises(FileNotFoundError):
        render.find_font(["/nope/font.ttc"])
