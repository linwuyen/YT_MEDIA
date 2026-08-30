from __future__ import annotations

from pathlib import Path
from typing import Any

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from .qc import best_thumbnail_frame


def set_best_thumbnail(
    youtube,
    video_id: str,
    video_path: Path,
    media_info: dict[str, Any],
    work_dir: Path,
) -> dict[str, Any]:
    target = work_dir / "thumbnail.jpg"
    selected = best_thumbnail_frame(video_path, media_info, target)
    if not selected:
        return {"ok": False, "reason": "no_candidate"}
    try:
        media = MediaFileUpload(str(target), mimetype="image/jpeg", resumable=False)
        response = youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
        return {"ok": True, "selection": selected, "response": response}
    except HttpError as exc:
        return {"ok": False, "reason": "youtube_rejected", "error": str(exc), "selection": selected}
    except Exception as exc:
        return {"ok": False, "reason": type(exc).__name__, "error": str(exc), "selection": selected}
