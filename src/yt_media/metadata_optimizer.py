from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .core import VideoItem, load_config, make_metadata, runtime_dir
from .google_api import build_drive, read_drive_state, write_drive_state

REPO_ROOT = Path(__file__).resolve().parents[2]
MANAGE_SCOPES = ["https://www.googleapis.com/auth/youtube"]


def _client_secret_path() -> Path:
    runtime = runtime_dir()
    return Path(os.environ.get("YT_MEDIA_CLIENT_SECRET_PATH", str(runtime / "client_secret.json")))


def manage_token_path() -> Path:
    runtime = runtime_dir()
    return Path(os.environ.get("YT_MEDIA_YOUTUBE_MANAGE_TOKEN_PATH", str(runtime / "youtube_manage_token.json")))


def _write_token(path: Path, creds: Credentials) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")


def _credentials(interactive: bool) -> Credentials:
    token_path = manage_token_path()
    creds: Credentials | None = None
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), scopes=MANAGE_SCOPES)
        except Exception:
            creds = None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _write_token(token_path, creds)
        except Exception:
            creds = None
    if creds and creds.valid:
        return creds
    if not interactive:
        raise RuntimeError(f"Metadata editor 尚未授權：{token_path.name}")

    client_secret = _client_secret_path()
    if not client_secret.exists():
        raise FileNotFoundError(f"找不到 {client_secret}")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), scopes=MANAGE_SCOPES)
    creds = flow.run_local_server(
        host="localhost",
        port=0,
        open_browser=True,
        access_type="offline",
        prompt="consent select_account",
        success_message="YouTube metadata authorization completed. You may close this window.",
    )
    _write_token(token_path, creds)
    return creds


def _validate_channel(youtube, config: dict[str, Any]) -> dict[str, str]:
    response = youtube.channels().list(part="id,snippet", mine=True, maxResults=50).execute()
    channels = [
        {"id": item.get("id", ""), "title": item.get("snippet", {}).get("title", "")}
        for item in response.get("items", [])
    ]
    expected = str(config["youtube_channel_id"])
    channel = next((item for item in channels if item["id"] == expected), None)
    if not channel:
        visible = ", ".join(f"{x['id']} ({x['title']})" for x in channels) or "沒有取得任何頻道"
        raise RuntimeError(f"Metadata OAuth 頻道不符。已授權：{visible}")
    return channel


def build_editor(config: dict[str, Any], interactive: bool = False):
    creds = _credentials(interactive)
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    channel = _validate_channel(youtube, config)
    return youtube, creds, channel


def authorize(config: dict[str, Any]) -> int:
    path = manage_token_path()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    _, _, channel = build_editor(config, interactive=True)
    print(json.dumps({"ok": True, "channel": channel, "token": str(path)}, ensure_ascii=False, indent=2))
    return 0


def doctor(config: dict[str, Any]) -> int:
    try:
        _, _, channel = build_editor(config, interactive=False)
        print(json.dumps({"ok": True, "channel": channel, "token": str(manage_token_path())}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "token": str(manage_token_path())}, ensure_ascii=False, indent=2))
        return 1


def _iso_duration_seconds(value: str) -> float:
    match = re.fullmatch(r"P(?:([0-9.]+)D)?T?(?:([0-9.]+)H)?(?:([0-9.]+)M)?(?:([0-9.]+)S)?", value or "")
    if not match:
        return 0.0
    days, hours, minutes, seconds = (float(x or 0) for x in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _media_hint(video: dict[str, Any]) -> dict[str, Any]:
    snippet = video.get("snippet", {})
    current_title = str(snippet.get("title", ""))
    duration = _iso_duration_seconds(str(video.get("contentDetails", {}).get("duration", "")))
    looked_like_short = "#Shorts" in current_title or (0 < duration <= 180)
    looked_like_4k = "4K" in current_title
    if looked_like_short:
        width, height = ((2160, 3840) if looked_like_4k else (1080, 1920))
    else:
        width, height = ((3840, 2160) if looked_like_4k else (1920, 1080))
    return {
        "format": {"duration": str(duration)},
        "streams": [{
            "codec_type": "video",
            "width": width,
            "height": height,
            "avg_frame_rate": "60/1" if "60fps" in current_title else "0/0",
        }],
    }


def _video_item_from_drive(drive, file_id: str) -> VideoItem:
    item = drive.files().get(
        fileId=file_id,
        fields="id,name,mimeType,size,parents,modifiedTime",
        supportsAllDrives=True,
    ).execute()
    parents = item.get("parents", [])
    parent = parents[0] if parents else ""
    return VideoItem(
        file_id=file_id,
        name=item.get("name", file_id),
        mime_type=item.get("mimeType", "video/mp4"),
        size=int(item.get("size") or 0),
        parent_id=parent,
        parent_name="",
        done_parent_id=parent,
        priority=False,
        modified_time=item.get("modifiedTime", ""),
    )


def _update_snippet(youtube, video_id: str, metadata: dict[str, Any], config: dict[str, Any]) -> None:
    body = {
        "id": video_id,
        "snippet": {
            "title": metadata["title"],
            "description": metadata["description"],
            "tags": metadata["tags"],
            "categoryId": str(config.get("category_id", "17")),
            "defaultLanguage": str(config.get("default_language", "zh-TW")),
        },
    }
    youtube.videos().update(part="snippet", body=body).execute()


def refresh(config: dict[str, Any]) -> int:
    if not manage_token_path().exists():
        print("Metadata editor token 尚未設定；略過舊影片 metadata refresh，不影響正常上傳。")
        return 0

    youtube, _, channel = build_editor(config, interactive=False)
    drive, _ = build_drive(False)
    root_id = str(config["drive_root_folder_id"])
    state, state_file_id = read_drive_state(drive, root_id)
    files_state = state.setdefault("files", {})
    target_version = int(config.get("metadata_version", 2))
    limit = int(config.get("metadata_refresh_max_per_run", 12))
    refreshed = 0
    marked_current = 0
    now = datetime.now(timezone.utc)

    for file_id, entry in files_state.items():
        if refreshed + marked_current >= limit:
            break
        if not isinstance(entry, dict):
            continue
        video_id = entry.get("youtube_video_id")
        if not video_id or int(entry.get("metadata_version") or 0) >= target_version:
            continue

        response = youtube.videos().list(part="snippet,status,contentDetails", id=video_id).execute()
        items = response.get("items", [])
        if not items:
            continue
        video = items[0]
        status = video.get("status", {})
        publish_at = status.get("publishAt")
        if status.get("privacyStatus") != "private" or not publish_at:
            continue
        try:
            publish_dt = datetime.fromisoformat(str(publish_at).replace("Z", "+00:00"))
        except ValueError:
            continue
        if publish_dt <= now:
            continue

        item = _video_item_from_drive(drive, file_id)
        metadata = make_metadata(item, _media_hint(video), config)
        snippet = video.get("snippet", {})
        current_tags = list(snippet.get("tags", []))
        if (
            snippet.get("title") == metadata["title"]
            and snippet.get("description", "") == metadata["description"]
            and current_tags == metadata["tags"]
        ):
            entry["metadata_version"] = target_version
            entry["metadata_checked_at"] = now.isoformat()
            marked_current += 1
        else:
            _update_snippet(youtube, video_id, metadata, config)
            entry["title"] = metadata["title"]
            entry["metadata_version"] = target_version
            entry["metadata_refreshed_at"] = now.isoformat()
            refreshed += 1
            print(json.dumps({
                "event": "metadata_refreshed",
                "file": item.name,
                "youtube_video_id": video_id,
                "title": metadata["title"],
                "hashtags": metadata["hashtags"],
            }, ensure_ascii=False))
        state_file_id = write_drive_state(drive, root_id, state, file_id=state_file_id)

    print(json.dumps({
        "ok": True,
        "channel": channel,
        "metadata_version": target_version,
        "refreshed": refreshed,
        "already_current": marked_current,
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="YT_MEDIA metadata optimizer")
    parser.add_argument("command", choices=["authorize", "doctor", "refresh"])
    args = parser.parse_args()
    config = load_config(REPO_ROOT)
    if args.command == "authorize":
        return authorize(config)
    if args.command == "doctor":
        return doctor(config)
    if args.command == "refresh":
        return refresh(config)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
