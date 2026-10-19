"""YouTube Data API v3: OAuth, playlist reading, and (later) upload / playlist edits.

Quota notes (default 10,000 units/day):
  *.list = 1, playlistItems.insert/delete = 50, videos.insert = 1600.
Every call goes through `_call` so `quota.used` is accurate for the run report.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError

from tldw.config import Secrets

SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
]

COST_LIST = 1
COST_PLAYLIST_ITEM_WRITE = 50
COST_PLAYLIST_INSERT = 50
COST_VIDEO_INSERT = 1600


class AuthError(RuntimeError):
    pass


class QuotaExceeded(RuntimeError):
    pass


@dataclass
class QuotaMeter:
    used: int = 0
    calls: dict[str, int] = field(default_factory=dict)

    def add(self, name: str, cost: int) -> None:
        self.used += cost
        self.calls[name] = self.calls.get(name, 0) + 1


quota = QuotaMeter()


# --- auth -------------------------------------------------------------------


def load_credentials(secrets: Secrets) -> Credentials | None:
    """Return saved credentials (refreshed if needed), or None if `tldw auth` never ran."""
    token_file = secrets.google_token_file
    if not token_file.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise AuthError(f"token refresh failed ({e}); run `tldw auth` again") from e
        _save_credentials(creds, token_file)
        return creds
    return None


def run_auth_flow(secrets: Secrets, *, open_browser: bool = True) -> Credentials:
    """Interactive OAuth (Desktop app client). Must run on a machine with a browser.

    With open_browser=False the URL is printed for you to open yourself; the redirect
    still lands on localhost of the machine running this command.
    """
    client_file = secrets.google_client_secret_file
    if not client_file.exists():
        raise AuthError(
            f"OAuth client secret not found at {client_file}. Create a Desktop-app OAuth client "
            "in Google Cloud Console (YouTube Data API v3 enabled), download the JSON, and set "
            "GOOGLE_CLIENT_SECRET_FILE in .env."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=open_browser, access_type="offline")
    _save_credentials(creds, secrets.google_token_file)
    return creds


def _save_credentials(creds: Credentials, path: Path) -> None:
    path.write_text(creds.to_json(), encoding="utf-8")
    path.chmod(0o600)


def build_service(creds: Credentials) -> Resource:
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def require_service(secrets: Secrets) -> Resource:
    creds = load_credentials(secrets)
    if creds is None:
        raise AuthError("not authenticated; run `tldw auth` first")
    return build_service(creds)


# --- helpers ----------------------------------------------------------------


def _call(request: Any, name: str, cost: int) -> dict[str, Any]:
    quota.add(name, cost)
    try:
        return request.execute()
    except HttpError as e:
        if _is_quota_error(e):
            raise QuotaExceeded(f"YouTube API quota exceeded during {name}") from e
        raise


def _is_quota_error(e: HttpError) -> bool:
    if e.resp.status != 403:
        return False
    try:
        body = json.loads(e.content.decode("utf-8"))
        reasons = {err.get("reason") for err in body.get("error", {}).get("errors", [])}
        return bool(reasons & {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"})
    except (ValueError, AttributeError):
        return "quota" in str(e).lower()


_DURATION_RE = re.compile(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def parse_duration(iso: str) -> int:
    """ISO 8601 duration (PT1H2M3S) -> seconds. Returns 0 for unparseable input."""
    m = _DURATION_RE.match(iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


# --- playlists ----------------------------------------------------------------


def find_playlist_id(yt: Resource, name: str) -> str | None:
    """ID of the authenticated user's playlist with this exact title, or None."""
    page_token = None
    while True:
        resp = _call(
            yt.playlists().list(part="snippet", mine=True, maxResults=50, pageToken=page_token),
            "playlists.list",
            COST_LIST,
        )
        for item in resp.get("items", []):
            if item["snippet"]["title"] == name:
                return item["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            return None


def create_playlist(yt: Resource, name: str, *, privacy: str = "private") -> str:
    resp = _call(
        yt.playlists().insert(
            part="snippet,status",
            body={"snippet": {"title": name}, "status": {"privacyStatus": privacy}},
        ),
        "playlists.insert",
        COST_PLAYLIST_INSERT,
    )
    return resp["id"]


@dataclass(frozen=True)
class PlaylistEntry:
    video_id: str
    playlist_item_id: str
    title: str
    channel: str | None
    position: int


def iter_playlist_items(yt: Resource, playlist_id: str) -> Iterator[PlaylistEntry]:
    page_token = None
    while True:
        resp = _call(
            yt.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=page_token,
            ),
            "playlistItems.list",
            COST_LIST,
        )
        for item in resp.get("items", []):
            sn = item["snippet"]
            title = sn.get("title", "")
            # Deleted / private videos show up with these titles and no owner channel.
            if title in {"Deleted video", "Private video"}:
                continue
            yield PlaylistEntry(
                video_id=item["contentDetails"]["videoId"],
                playlist_item_id=item["id"],
                title=title,
                channel=sn.get("videoOwnerChannelTitle"),
                position=sn.get("position", 0),
            )
        page_token = resp.get("nextPageToken")
        if not page_token:
            return


@dataclass(frozen=True)
class VideoDetails:
    video_id: str
    title: str
    description: str
    channel: str
    tags: list[str]
    duration_s: int
    language: str | None  # defaultAudioLanguage or defaultLanguage, if the uploader set one
    published_at: str


def fetch_video_details(yt: Resource, video_ids: list[str]) -> dict[str, VideoDetails]:
    """videos.list in batches of 50 (1 unit each). Missing/unavailable IDs are omitted."""
    out: dict[str, VideoDetails] = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        resp = _call(
            yt.videos().list(part="snippet,contentDetails", id=",".join(batch), maxResults=50),
            "videos.list",
            COST_LIST,
        )
        for item in resp.get("items", []):
            sn = item["snippet"]
            out[item["id"]] = VideoDetails(
                video_id=item["id"],
                title=sn.get("title", ""),
                description=sn.get("description", ""),
                channel=sn.get("channelTitle", ""),
                tags=list(sn.get("tags", [])),
                duration_s=parse_duration(item["contentDetails"].get("duration", "")),
                language=sn.get("defaultAudioLanguage") or sn.get("defaultLanguage"),
                published_at=sn.get("publishedAt", ""),
            )
    return out


def delete_playlist_item(yt: Resource, playlist_item_id: str) -> None:
    _call(
        yt.playlistItems().delete(id=playlist_item_id),
        "playlistItems.delete",
        COST_PLAYLIST_ITEM_WRITE,
    )
