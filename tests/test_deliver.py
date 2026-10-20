import json
from types import SimpleNamespace

import pytest
import requests

from tldw import youtube
from tldw.deliver import common, telegram
from tldw.deliver import youtube as dyt
from tldw.script import Point, Script


def make_script(title="CPU caches"):
    return Script(
        title=title,
        language="en",
        context="Ctx.",
        points=[Point(claim=f"Claim {i}.", detail="d", timestamp_s=i * 75) for i in range(1, 4)],
        takeaway="Keep hot data small.",
    )


# --- common -------------------------------------------------------------------


def test_title_fits_youtube_limit():
    s = make_script()
    short = common.title(s, "Systems design", "Orig")
    assert short == '[Systems design] CPU caches — TLDW of "Orig"'
    long_title = common.title(s, "Systems design", "x" * 200)
    assert len(long_title) <= 100
    assert long_title.startswith("[Systems design] CPU caches — TLDW of")


def test_description_has_timestamps_and_source():
    d = common.description(make_script(), "https://youtu.be/x", "Orig", "Chan")
    assert d.startswith("Keep hot data small.")
    assert 'Summary of "Orig" by Chan' in d
    assert "1:15 Claim 1." in d and "3:45 Claim 3." in d
    assert "https://youtu.be/x" in d


def test_telegram_caption_clipped():
    s = make_script()
    s = s.model_copy(update={"takeaway": "y" * 2000})
    assert len(common.telegram_caption(s, "T", "https://u")) == 1024


# --- youtube upload -------------------------------------------------------------


class _Req:
    def __init__(self, payload, log, name, kwargs):
        self.payload, self.log, self.name, self.kwargs = payload, log, name, kwargs

    def execute(self):
        self.log.append((self.name, self.kwargs))
        return self.payload


class _FakeYT:
    def __init__(self):
        self.log = []

    def videos(self):
        return SimpleNamespace(insert=lambda **kw: _Req({"id": "NEWVID"}, self.log, "v", kw))

    def playlistItems(self):  # noqa: N802
        return SimpleNamespace(insert=lambda **kw: _Req({"id": "PI"}, self.log, "pi", kw))


def test_upload_short_private_then_playlist(tmp_path):
    yt = _FakeYT()
    before = youtube.quota.used
    media = []
    vid = dyt.upload_short(
        yt,
        tmp_path / "x.mp4",
        title="T",
        description="D",
        language="ko",
        playlist_id="PLOUT",
        media_factory=lambda path, **kw: media.append((path, kw)) or "MEDIA",
    )
    assert vid == "NEWVID"
    (_, vkw), (_, pkw) = yt.log
    assert vkw["body"]["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False}
    assert vkw["body"]["snippet"]["defaultAudioLanguage"] == "ko"
    assert vkw["media_body"] == "MEDIA" and media[0][1]["resumable"] is True
    assert pkw["body"]["snippet"]["playlistId"] == "PLOUT"
    assert pkw["body"]["snippet"]["resourceId"]["videoId"] == "NEWVID"
    assert youtube.quota.used - before == 1650


def test_ensure_playlist_creates_when_missing(monkeypatch):
    monkeypatch.setattr(youtube, "find_playlist_id", lambda yt, name: None)
    monkeypatch.setattr(youtube, "create_playlist", lambda yt, name: f"new:{name}")
    assert dyt.ensure_playlist(object(), "TLDW Shorts") == "new:TLDW Shorts"


# --- telegram ---------------------------------------------------------------------


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError
        return self._body


def test_send_video_ok(tmp_path):
    video = tmp_path / "x.mp4"
    video.write_bytes(b"mp4")
    seen = {}

    def post(url, data, files, timeout):
        seen.update(url=url, data=data, name=files["video"][0])
        return _Resp(200, {"ok": True, "result": {"message_id": 42}})

    assert telegram.send_video("TOK", "123", video, "cap", duration_s=90, post=post) == 42
    assert seen["url"].endswith("/botTOK/sendVideo")
    assert seen["data"]["chat_id"] == "123" and seen["data"]["duration"] == "90"
    assert seen["name"] == "x.mp4"


def test_send_video_api_error(tmp_path):
    video = tmp_path / "x.mp4"
    video.write_bytes(b"mp4")
    post = lambda *a, **k: _Resp(400, {"ok": False, "description": "chat not found"})  # noqa: E731
    with pytest.raises(telegram.TelegramError, match="chat not found"):
        telegram.send_video("TOK", "1", video, "c", post=post)


def test_send_video_network_error_hides_token(tmp_path):
    video = tmp_path / "x.mp4"
    video.write_bytes(b"mp4")

    def post(*a, **k):
        raise requests.ConnectionError("https://api.telegram.org/botSECRET/sendVideo failed")

    with pytest.raises(telegram.TelegramError) as e:
        telegram.send_video("SECRET", "1", video, "c", post=post)
    assert "SECRET" not in str(e.value)


def test_send_video_too_big(tmp_path, monkeypatch):
    video = tmp_path / "x.mp4"
    video.write_bytes(b"0")
    monkeypatch.setattr(telegram, "MAX_UPLOAD_BYTES", 0)
    with pytest.raises(telegram.TelegramError, match="50 MB"):
        telegram.send_video("T", "1", video, "c", post=None)


def test_json_roundtrip_of_script_unchanged():
    # guard: description generation must not mutate the script
    s = make_script()
    before = json.loads(s.model_dump_json())
    common.description(s, "u", "o", None)
    assert json.loads(s.model_dump_json()) == before
