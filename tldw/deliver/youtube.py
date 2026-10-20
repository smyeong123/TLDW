"""Upload a finished short as a Private video and add it to the output playlist.

Quota: videos.insert ~1600 units, playlistItems.insert 50. QuotaExceeded propagates so
the caller can stop the run gracefully and leave the rest for tomorrow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from googleapiclient.discovery import Resource
from googleapiclient.http import MediaFileUpload

from tldw import youtube
from tldw.youtube import COST_PLAYLIST_ITEM_WRITE, COST_VIDEO_INSERT, _call


def ensure_playlist(yt: Resource, name: str) -> str:
    return youtube.find_playlist_id(yt, name) or youtube.create_playlist(yt, name)


def upload_short(
    yt: Resource,
    video_path: Path,
    *,
    title: str,
    description: str,
    language: str | None,
    playlist_id: str,
    media_factory: Any = MediaFileUpload,
) -> str:
    """Returns the new video id."""
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": "22",  # People & Blogs: neutral; TLDW summarizes any kind of video
            **({"defaultLanguage": language, "defaultAudioLanguage": language} if language else {}),
        },
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False},
    }
    media = media_factory(str(video_path), mimetype="video/mp4", resumable=True)
    resp = _call(
        yt.videos().insert(part="snippet,status", body=body, media_body=media),
        "videos.insert",
        COST_VIDEO_INSERT,
    )
    video_id = resp["id"]
    _call(
        yt.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        ),
        "playlistItems.insert",
        COST_PLAYLIST_ITEM_WRITE,
    )
    return video_id
