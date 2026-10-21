import json
from types import SimpleNamespace

from googleapiclient.errors import HttpError

from tldw import migrate, youtube
from tldw.youtube import PlaylistEntry

NEW_CSV = """\ufeffVideo ID,Playlist Video Creation Timestamp
aaaaaaaaaaa,2024-01-01T00:00:00+00:00
bbbbbbbbbbb,2024-01-02T00:00:00+00:00
aaaaaaaaaaa,2024-01-03T00:00:00+00:00
"""

OLD_CSV = """Playlist ID,Channel ID,Time Created,Time Updated,Title,Description,Visibility
WL,UCx,2020-01-01,2020-01-02,Watch later,,Private

Video ID,Time Added
ccccccccccc,2020-01-01 00:00:00 UTC
not-an-id,2020-01-01 00:00:00 UTC

"""


def test_parse_takeout_new_and_old_layouts():
    assert migrate.parse_takeout_csv(NEW_CSV) == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert migrate.parse_takeout_csv(OLD_CSV) == ["ccccccccccc"]
    assert migrate.parse_takeout_csv("nothing here") == []


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


def _yt(behaviour):
    added = []

    def insert(part, body):
        vid = body["snippet"]["resourceId"]["videoId"]

        def run():
            outcome = behaviour.get(vid, "ok")
            if outcome == "quota":
                raise youtube.QuotaExceeded("q")
            if outcome == "404":
                raise HttpError(SimpleNamespace(status=404, reason="nf"), json.dumps({}).encode())
            added.append(vid)
            return {"id": "PI"}

        return _Req(run)

    return SimpleNamespace(playlistItems=lambda: SimpleNamespace(insert=insert)), added


def test_migrate_skips_present_and_stops_on_quota(monkeypatch):
    monkeypatch.setattr(youtube, "find_playlist_id", lambda yt, name: "PL")
    monkeypatch.setattr(
        youtube,
        "iter_playlist_items",
        lambda yt, pid: iter([PlaylistEntry("aaaaaaaaaaa", "p", "t", None, 0)]),
    )
    yt, added = _yt({"ccccccccccc": "404", "ddddddddddd": "quota"})
    ids = ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc", "ddddddddddd", "eeeeeeeeeee"]
    r = migrate.migrate(yt, ids, "TLDW", dry_run=False)
    assert r.already == ["aaaaaaaaaaa"]
    assert r.added == added == ["bbbbbbbbbbb"]
    assert r.failed == {"ccccccccccc": "HTTP 404"}
    assert r.remaining == ["ddddddddddd", "eeeeeeeeeee"]


def test_migrate_dry_run_adds_nothing(monkeypatch):
    monkeypatch.setattr(youtube, "find_playlist_id", lambda yt, name: None)
    yt, added = _yt({})
    r = migrate.migrate(yt, ["aaaaaaaaaaa"], "TLDW", dry_run=True)
    assert r.remaining == ["aaaaaaaaaaa"] and added == []
