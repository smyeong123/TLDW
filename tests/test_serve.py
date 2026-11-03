import json
import threading
import urllib.error
import urllib.request

import pytest

from tldw.serve import make_server
from tldw.state import State

TOKEN = "s3cret"
SNAP = {
    "version": 1,
    "source": "watch_later",
    "taken_at": "2026-10-20T01:00:00Z",
    "items": [{"video_id": "aaaaaaaaaaa", "title": "A", "watched_pct": 0.1}],
}


@pytest.fixture
def server(tmp_path):
    srv = make_server(tmp_path / "state.db", TOKEN, 0)  # port 0 = any free port
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, tmp_path / "state.db"
    srv.shutdown()
    srv.server_close()


def _req(srv, path, *, body=None, token=TOKEN):
    host, port = srv.server_address
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://{host}:{port}{path}", data=data)
    req.add_header("X-TLDW-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_health(server):
    srv, _ = server
    assert _req(srv, "/health") == (200, {"ok": True})


def test_snapshot_ingested(server):
    srv, db = server
    assert _req(srv, "/snapshot", body=SNAP) == (200, {"new": 1, "updated": 0})
    with State(db) as s:
        assert s.get("aaaaaaaaaaa").watched_pct == 0.1


def test_bad_token_rejected(server):
    srv, db = server
    status, _ = _req(srv, "/snapshot", body=SNAP, token="wrong")
    assert status == 401
    with State(db) as s:
        assert s.get("aaaaaaaaaaa") is None


def test_bad_snapshot_is_400(server):
    srv, _ = server
    status, body = _req(srv, "/snapshot", body={"version": 1})
    assert status == 400 and "invalid snapshot" in body["error"]


def test_binds_localhost_only(server):
    srv, _ = server
    assert srv.server_address[0] == "127.0.0.1"


def test_empty_token_refused(tmp_path):
    with pytest.raises(ValueError):
        make_server(tmp_path / "s.db", "", 0)


def _get(srv, path):
    host, port = srv.server_address
    try:
        with urllib.request.urlopen(f"http://{host}:{port}{path}", timeout=5) as resp:
            return resp.status, resp.headers["Content-Type"], resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.headers["Content-Type"], e.read().decode()


def test_serves_snippet_pages(tmp_path):
    pages = tmp_path / "snippets"
    pages.mkdir()
    (pages / "index.html").write_text("<h1>index</h1>")
    (pages / "aaaaaaaaaaa.html").write_text("<h1>page</h1>")
    (tmp_path / "secret.html").write_text("nope")
    srv = make_server(tmp_path / "state.db", TOKEN, 0, pages_dir=pages)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert _get(srv, "/")[0::2] == (200, "<h1>index</h1>")
        status, ctype, body = _get(srv, "/s/aaaaaaaaaaa?x=1")
        assert (status, body) == (200, "<h1>page</h1>") and ctype.startswith("text/html")
        assert _get(srv, "/s/bbbbbbbbbbb")[0] == 404
        assert _get(srv, "/s/../secret")[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()
