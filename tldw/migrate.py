"""`tldw migrate-wl`: copy a Google Takeout Watch Later export into the input playlist.

Only useful with `source: playlist` (the extension reads Watch Later directly). Each add
is playlistItems.insert = 50 quota units, so ~190 videos/day fit in the default quota; on
QuotaExceeded it stops and a rerun the next day continues where it left off.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError

from tldw import youtube
from tldw.deliver.youtube import ensure_playlist
from tldw.youtube import COST_PLAYLIST_ITEM_WRITE, _call

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def parse_takeout_csv(text: str) -> list[str]:
    """Video ids from a Takeout playlist CSV ('Watch later-videos.csv' and older layouts).

    Takeout has shipped several layouts; all have a header row starting with 'Video ID'
    followed by one video per row. Anything before that header (old metadata block) is
    skipped. Order is kept and duplicates dropped.
    """
    ids: list[str] = []
    in_videos = False
    for row in csv.reader(io.StringIO(text.lstrip("﻿"))):
        first = row[0].strip() if row else ""
        if first.casefold() == "video id":
            in_videos = True
            continue
        if not in_videos:
            continue
        if not first:
            in_videos = False  # blank line ends the block
            continue
        if _VIDEO_ID.match(first) and first not in ids:
            ids.append(first)
    return ids


@dataclass
class MigrateResult:
    added: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)  # not done because of quota
    failed: dict[str, str] = field(default_factory=dict)


def migrate(yt: Resource, ids: list[str], playlist_name: str, *, dry_run: bool) -> MigrateResult:
    result = MigrateResult()
    playlist_id = youtube.find_playlist_id(yt, playlist_name)
    present = (
        {e.video_id for e in youtube.iter_playlist_items(yt, playlist_id)} if playlist_id else set()
    )
    todo = []
    for vid in ids:
        (result.already if vid in present else todo).append(vid)
    if dry_run:
        result.remaining = todo
        return result
    if todo and playlist_id is None:
        playlist_id = ensure_playlist(yt, playlist_name)
    for i, vid in enumerate(todo):
        try:
            _call(
                yt.playlistItems().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "playlistId": playlist_id,
                            "resourceId": {"kind": "youtube#video", "videoId": vid},
                        }
                    },
                ),
                "playlistItems.insert",
                COST_PLAYLIST_ITEM_WRITE,
            )
        except youtube.QuotaExceeded:
            result.remaining = todo[i:]
            return result
        except HttpError as e:  # deleted/private videos can't be added
            result.failed[vid] = f"HTTP {e.resp.status}"
            continue
        result.added.append(vid)
    return result
