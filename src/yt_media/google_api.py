from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Any, Callable

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from .core import VideoItem, runtime_dir, to_utc_iso

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def _credential_paths() -> tuple[Path, Path, Path]:
    runtime = runtime_dir()
    client = Path(os.environ.get("YT_MEDIA_CLIENT_SECRET_PATH", str(runtime / "client_secret.json")))
    drive = Path(os.environ.get("YT_MEDIA_DRIVE_TOKEN_PATH", str(runtime / "drive_token.json")))
    youtube = Path(os.environ.get("YT_MEDIA_YOUTUBE_TOKEN_PATH", str(runtime / "youtube_token.json")))
    return client, drive, youtube


def _write_token_if_possible(token_path: Path, creds: Credentials) -> None:
    try:
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
    except OSError:
        # Cloud Run mounts Secret Manager files read-only. A refreshed access
        # token only needs to live for this process; the refresh token stored in
        # Secret Manager remains sufficient for the next execution.
        pass


def _credentials(token_path: Path, scopes: list[str], interactive: bool) -> Credentials:
    client_secret, _, _ = _credential_paths()
    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes=scopes)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _write_token_if_possible(token_path, creds)
        except Exception:
            creds = None
    if creds and creds.valid:
        return creds
    if not interactive:
        raise RuntimeError(f"尚未授權：{token_path.name}")
    if not client_secret.exists():
        raise FileNotFoundError(f"找不到 {client_secret}")

    # Drive and YouTube intentionally use separate tokens. Do not enable
    # incremental authorization, because Google can otherwise return a union of
    # previously granted scopes and oauthlib rejects that as a scope mismatch.
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), scopes=scopes)
    return flow.run_local_server(
        host="localhost",
        port=0,
        open_browser=True,
        access_type="offline",
        prompt="consent select_account",
        success_message="The authentication flow has completed. You may close this window.",
    )


def build_drive(interactive: bool = False):
    _, drive_token, _ = _credential_paths()
    creds = _credentials(drive_token, DRIVE_SCOPES, interactive)
    return build("drive", "v3", credentials=creds, cache_discovery=False), creds


def build_youtube(config: dict[str, Any], interactive: bool = False):
    _, _, youtube_token = _credential_paths()
    creds = _credentials(youtube_token, YOUTUBE_SCOPES, interactive)
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    response = youtube.channels().list(part="id,snippet", mine=True, maxResults=50).execute()
    channels = [
        {"id": item.get("id", ""), "title": item.get("snippet", {}).get("title", "")}
        for item in response.get("items", [])
    ]
    expected = str(config["youtube_channel_id"])
    channel = next((item for item in channels if item["id"] == expected), None)
    if not channel:
        if interactive:
            try:
                youtube_token.unlink(missing_ok=True)
            except OSError:
                pass
        visible = ", ".join(f"{x['id']} ({x['title']})" for x in channels) or "沒有取得任何頻道"
        raise RuntimeError(f"OAuth 頻道不符。已授權：{visible}")
    return youtube, creds, channel


def authorize_drive() -> dict[str, Any]:
    _, drive_token, _ = _credential_paths()
    drive, creds = build_drive(interactive=True)
    about = drive.about().get(fields="user(displayName,emailAddress)").execute()
    _write_token_if_possible(drive_token, creds)
    return about.get("user", {})


def authorize_youtube(config: dict[str, Any]) -> dict[str, Any]:
    _, _, youtube_token = _credential_paths()
    try:
        youtube_token.unlink(missing_ok=True)
    except OSError:
        pass
    _, creds, channel = build_youtube(config, interactive=True)
    _write_token_if_possible(youtube_token, creds)
    return channel


def list_children(drive, folder_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    token = None
    while True:
        response = drive.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            spaces="drive",
            fields="nextPageToken,files(id,name,mimeType,size,parents,modifiedTime)",
            pageToken=token,
            pageSize=1000,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        result.extend(response.get("files", []))
        token = response.get("nextPageToken")
        if not token:
            return result


def discover_videos(drive, config: dict[str, Any]) -> list[VideoItem]:
    root_id = str(config["drive_root_folder_id"])
    excluded = set(config.get("excluded_folder_names", []))
    priority_name = str(config.get("priority_folder_name", "01_優先上傳"))
    folder_mime = "application/vnd.google-apps.folder"
    queue: list[tuple[str, str, str | None]] = [(root_id, "ROOT", None)]
    visited: set[str] = set()
    videos: list[VideoItem] = []

    while queue:
        folder_id, folder_name, parent_id = queue.pop(0)
        if folder_id in visited:
            continue
        visited.add(folder_id)
        children = list_children(drive, folder_id)
        is_priority = folder_name == priority_name
        done_parent_id = parent_id if is_priority and parent_id else folder_id

        for child in children:
            name = child.get("name", "")
            mime = child.get("mimeType", "")
            if mime == folder_mime:
                if name in excluded:
                    continue
                queue.append((child["id"], name, folder_id))
                continue
            if not (mime.startswith("video/") or Path(name).suffix.lower() in {".mp4", ".mov", ".mkv"}):
                continue
            videos.append(VideoItem(
                file_id=child["id"],
                name=name,
                mime_type=mime or "video/mp4",
                size=int(child.get("size") or 0),
                parent_id=folder_id,
                parent_name=folder_name,
                done_parent_id=done_parent_id,
                priority=is_priority,
                modified_time=child.get("modifiedTime", ""),
            ))

    videos.sort(key=lambda item: (0 if item.priority else 1, item.modified_time, item.name))
    return videos


def ensure_folder(drive, parent_id: str, name: str) -> str:
    for item in list_children(drive, parent_id):
        if item.get("mimeType") == "application/vnd.google-apps.folder" and item.get("name") == name:
            return item["id"]
    response = drive.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
        fields="id",
        supportsAllDrives=True,
    ).execute()
    return response["id"]


def download_file(drive, file_id: str, target: Path, progress: Callable[[float], None] | None = None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    with target.open("wb") as handle:
        downloader = MediaIoBaseDownload(handle, request, chunksize=8 * 1024 * 1024)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status and progress:
                progress(status.progress())


def move_file(drive, file_id: str, old_parent_id: str, new_parent_id: str) -> None:
    drive.files().update(
        fileId=file_id,
        addParents=new_parent_id,
        removeParents=old_parent_id,
        fields="id,parents",
        supportsAllDrives=True,
    ).execute()


def existing_publish_times(youtube, max_videos: int = 300) -> set[str]:
    response = youtube.channels().list(part="contentDetails", mine=True).execute()
    items = response.get("items", [])
    if not items:
        return set()
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    ids: list[str] = []
    token = None
    while len(ids) < max_videos:
        page = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=uploads,
            maxResults=50,
            pageToken=token,
        ).execute()
        ids.extend(item["contentDetails"]["videoId"] for item in page.get("items", []))
        token = page.get("nextPageToken")
        if not token:
            break
    result: set[str] = set()
    for start in range(0, len(ids), 50):
        page = youtube.videos().list(part="status", id=",".join(ids[start:start + 50])).execute()
        for item in page.get("items", []):
            value = item.get("status", {}).get("publishAt")
            if value:
                result.add(value)
    return result


def upload_video(youtube, path: Path, metadata: dict[str, Any], publish_at, config: dict[str, Any], progress=None) -> dict[str, Any]:
    body = {
        "snippet": {
            "title": metadata["title"],
            "description": metadata["description"],
            "tags": metadata["tags"],
            "categoryId": str(config.get("category_id", "17")),
            "defaultLanguage": str(config.get("default_language", "zh-TW")),
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": to_utc_iso(publish_at),
            "selfDeclaredMadeForKids": bool(config.get("made_for_kids", False)),
        },
    }
    mime = mimetypes.guess_type(str(path))[0] or "video/mp4"
    media = MediaFileUpload(str(path), mimetype=mime, chunksize=8 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
        notifySubscribers=bool(config.get("notify_subscribers", True)),
    )
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status and progress:
            progress(status.progress())
    return response


def processing_status(youtube, video_id: str) -> str:
    response = youtube.videos().list(part="status,processingDetails", id=video_id).execute()
    items = response.get("items", [])
    if not items:
        return "missing"
    status = items[0].get("status", {}).get("uploadStatus")
    processing = items[0].get("processingDetails", {}).get("processingStatus")
    return str(processing or status or "unknown")
