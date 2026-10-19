"""Unit tests for the pure/helper parts of tldw.youtube with a fake API resource."""

from tldw import youtube
from tldw.youtube import PlaylistEntry, VideoDetails, parse_duration


def test_parse_duration():
    assert parse_duration("PT1H2M3S") == 3723
    assert parse_duration("PT15M") == 900
    assert parse_duration("PT45S") == 45
    assert parse_duration("P1DT1H") == 90000
    assert parse_duration("") == 0
    assert parse_duration("garbage") == 0


class _Req:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _Pages:
    """Returns successive pages for repeated .list() calls."""

    def __init__(self, pages):
        self._pages = list(pages)

    def list(self, **kwargs):
        idx = 0 if kwargs.get("pageToken") is None else int(kwargs["pageToken"])
        return _Req(self._pages[idx])


class _FakeYT:
    def __init__(self, playlists=(), items=(), videos=()):
        self._playlists = _Pages(playlists)
        self._items = _Pages(items)
        self._videos = _Pages(videos)

    def playlists(self):
        return self._playlists

    def playlistItems(self):  # noqa: N802 - mirrors googleapiclient naming
        return self._items

    def videos(self):
        return self._videos


def test_find_playlist_id_paginates():
    yt = _FakeYT(
        playlists=[
            {"items": [{"id": "A", "snippet": {"title": "Other"}}], "nextPageToken": "1"},
            {"items": [{"id": "B", "snippet": {"title": "TLDW"}}]},
        ]
    )
    assert youtube.find_playlist_id(yt, "TLDW") == "B"
    assert youtube.find_playlist_id(yt, "Nope") is None


def test_iter_playlist_items_skips_deleted():
    yt = _FakeYT(
        items=[
            {
                "items": [
                    {
                        "id": "pi1",
                        "snippet": {
                            "title": "Good video",
                            "videoOwnerChannelTitle": "Chan",
                            "position": 0,
                        },
                        "contentDetails": {"videoId": "v1"},
                    },
                    {
                        "id": "pi2",
                        "snippet": {"title": "Deleted video", "position": 1},
                        "contentDetails": {"videoId": "v2"},
                    },
                ]
            }
        ]
    )
    got = list(youtube.iter_playlist_items(yt, "PL"))
    assert got == [PlaylistEntry("v1", "pi1", "Good video", "Chan", 0)]


def test_fetch_video_details_batches_and_parses():
    ids = [f"v{i}" for i in range(60)]
    pages = [
        {
            "items": [
                {
                    "id": vid,
                    "snippet": {
                        "title": f"T {vid}",
                        "description": "d",
                        "channelTitle": "C",
                        "tags": ["a"],
                        "defaultAudioLanguage": "ko",
                        "publishedAt": "2026-01-01T00:00:00Z",
                    },
                    "contentDetails": {"duration": "PT10M"},
                }
                for vid in chunk
            ]
        }
        for chunk in (ids[:50], ids[50:])
    ]

    class _Videos:
        def __init__(self):
            self.calls = 0

        def list(self, **kwargs):
            page = pages[self.calls]
            self.calls += 1
            return _Req(page)

    class _YT(_FakeYT):
        def __init__(self):
            super().__init__()
            self._v = _Videos()

        def videos(self):
            return self._v

    yt = _YT()
    before = youtube.quota.used
    out = youtube.fetch_video_details(yt, ids)
    assert len(out) == 60
    assert yt._v.calls == 2
    assert youtube.quota.used - before == 2
    assert out["v3"] == VideoDetails(
        "v3", "T v3", "d", "C", ["a"], 600, "ko", "2026-01-01T00:00:00Z"
    )
