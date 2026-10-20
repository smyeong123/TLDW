"""Send a finished short to a Telegram chat via the Bot API (sendVideo, 50 MB upload cap)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

API = "https://api.telegram.org/bot{token}/sendVideo"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


class TelegramError(RuntimeError):
    pass


def send_video(
    token: str,
    chat_id: str,
    video_path: Path,
    caption: str,
    *,
    width: int = 1080,
    height: int = 1920,
    duration_s: int | None = None,
    post: Any = requests.post,
) -> int:
    """Returns the Telegram message id."""
    if video_path.stat().st_size > MAX_UPLOAD_BYTES:
        raise TelegramError(f"{video_path.name} is over Telegram's 50 MB bot upload limit")
    data = {
        "chat_id": chat_id,
        "caption": caption,
        "supports_streaming": "true",
        "width": str(width),
        "height": str(height),
    }
    if duration_s:
        data["duration"] = str(duration_s)
    with video_path.open("rb") as f:
        try:
            resp = post(
                API.format(token=token),
                data=data,
                files={"video": (video_path.name, f, "video/mp4")},
                timeout=300,
            )
        except requests.RequestException as e:
            # never include the URL: it contains the bot token
            raise TelegramError(f"network error: {type(e).__name__}") from None
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code != 200 or not body.get("ok"):
        raise TelegramError(f"sendVideo failed ({resp.status_code}): {body.get('description')}")
    return body["result"]["message_id"]
