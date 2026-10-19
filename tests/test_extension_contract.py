"""The extension's buildSnapshot() output must be accepted by intake.parse_snapshot()."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tldw import intake

PARSE_JS = Path(__file__).resolve().parent.parent / "extension" / "parse.js"

SCRIPT = """
const P = require(process.argv[1]);
const rows = [
  {href: "/watch?v=aaaaaaaaaaa&list=WL", title: "A", channel: "C",
   durationText: "1:02:03", progressWidth: "55%"},
  {href: "/watch?v=bbbbbbbbbbb", title: "B", durationText: "LIVE"},
];
console.log(JSON.stringify(P.buildSnapshot(rows, new Date("2026-10-20T01:00:00Z"))));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_snapshot_parses_in_python():
    out = subprocess.run(
        ["node", "-e", SCRIPT, str(PARSE_JS)], capture_output=True, text=True, check=True
    ).stdout
    snap = intake.parse_snapshot(json.loads(out))
    assert snap.taken_at == "2026-10-20T01:00:00+00:00"
    assert [(i.video_id, i.duration_s, i.watched_pct) for i in snap.items] == [
        ("aaaaaaaaaaa", 3723, 0.55),
        ("bbbbbbbbbbb", None, 0.0),
    ]
