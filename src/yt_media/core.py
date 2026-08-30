from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class VideoItem:
    file_id: str
    name: str
    mime_type: str
    size: int
    parent_id: str
    parent_name: str
    done_parent_id: str
    priority: bool
    modified_time: str


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def runtime_dir() -> Path:
    explicit = os.environ.get("YT_MEDIA_RUNTIME_DIR")
    if explicit:
        return Path(explicit)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".yt_media")
    return Path(base) / "YT_MEDIA"


def load_config(repo_root: Path) -> dict[str, Any]:
    default_path = repo_root / "config" / "default.json"
    config = json.loads(default_path.read_text(encoding="utf-8"))
    override_path = runtime_dir() / "settings.json"
    if override_path.exists():
        config = deep_merge(config, json.loads(override_path.read_text(encoding="utf-8")))
    return config


def read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"files": {}}
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or not isinstance(state.get("files", {}), dict):
        raise ValueError(f"Invalid state document: {path}")
    state.setdefault("files", {})
    return state


def write_state(path: Path, state: dict[str, Any]) -> None:
    payload = json.dumps(state, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(payload, encoding="utf-8")
    temp.replace(path)


def merge_state_documents(local: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    """Merge migration state without ever changing an established YouTube ID."""
    merged: dict[str, Any] = {"files": {}}
    local_files = local.get("files", {}) if isinstance(local, dict) else {}
    remote_files = remote.get("files", {}) if isinstance(remote, dict) else {}
    if not isinstance(local_files, dict) or not isinstance(remote_files, dict):
        raise ValueError("State documents must contain a 'files' object")

    for file_id in set(local_files) | set(remote_files):
        left = local_files.get(file_id, {})
        right = remote_files.get(file_id, {})
        if not isinstance(left, dict) or not isinstance(right, dict):
            raise ValueError(f"Invalid state entry for Drive file {file_id}")

        left_video = left.get("youtube_video_id")
        right_video = right.get("youtube_video_id")
        if left_video and right_video and left_video != right_video:
            raise RuntimeError(
                f"State conflict for Drive file {file_id}: "
                f"local YouTube ID {left_video} != Drive-state YouTube ID {right_video}"
            )

        entry = dict(left)
        for key, value in right.items():
            if value not in (None, ""):
                entry[key] = value
        if left_video and not entry.get("youtube_video_id"):
            entry["youtube_video_id"] = left_video
        if left.get("moved") or right.get("moved"):
            entry["moved"] = True
            if right.get("status") == "done" or left.get("status") == "done":
                entry["status"] = "done"
        merged["files"][file_id] = entry

    return merged


def parse_recording_date(filename: str) -> str:
    match = re.search(r"(20\d{2})(\d{2})(\d{2})", filename)
    if not match:
        return ""
    return f"{match.group(1)}.{match.group(2)}.{match.group(3)}"


def detect_person(filename: str, known_people: Iterable[str]) -> str | None:
    normalized = re.sub(r"[\s_\-]", "", filename).lower()
    for person in known_people:
        if re.sub(r"[\s_\-]", "", person).lower() in normalized:
            return person
    return None


def is_short(media_info: dict[str, Any]) -> bool:
    try:
        duration = float(media_info.get("format", {}).get("duration") or 0)
        video = next(s for s in media_info.get("streams", []) if s.get("codec_type") == "video")
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        return bool(width and height and height > width and 0 < duration <= 180)
    except (StopIteration, TypeError, ValueError):
        return False


def make_metadata(item: VideoItem, media_info: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    person = detect_person(item.name, config.get("known_people", []))
    date_text = parse_recording_date(item.name)
    digest = int(hashlib.sha256(item.file_id.encode("utf-8")).hexdigest()[:8], 16)

    if person:
        templates = config["named_title_templates"]
        title = templates[digest % len(templates)].format(person=person)
        intro = f"{date_text} {person}球場應援直拍🔥" if date_text else f"{person}球場應援直拍🔥"
        tags = [person, *config.get("hashtags", [])]
    else:
        templates = config["generic_title_templates"]
        title = templates[digest % len(templates)]
        intro = f"{date_text} 球場啦啦隊應援直拍🔥" if date_text else "球場啦啦隊應援直拍🔥"
        tags = list(config.get("hashtags", []))

    short = is_short(media_info)
    if short and "#Shorts" not in title:
        title += " #Shorts"

    hashtags = " ".join("#" + tag.lstrip("#").replace(" ", "") for tag in tags)
    description = (
        f"{intro}\n"
        "4K 高畫質記錄現場舞蹈、表情與球場應援氣氛。\n\n"
        "你最喜歡哪個瞬間？留言告訴我👇\n"
        "喜歡球場應援直拍，記得訂閱「象兒應援團」！\n\n"
        f"{hashtags}"
    )
    return {
        "title": title[:100],
        "description": description,
        "tags": list(dict.fromkeys(tag.lstrip("#") for tag in tags))[:15],
        "person": person,
        "recording_date": date_text,
        "is_short": short,
    }


def schedule_slots(
    config: dict[str, Any],
    count: int,
    occupied_publish_at: Iterable[str],
    now: datetime | None = None,
) -> list[datetime]:
    tz = ZoneInfo(str(config["timezone"]))
    hh, mm = (int(x) for x in str(config["publish_time"]).split(":", 1))
    now = now.astimezone(tz) if now else datetime.now(tz)
    lead = timedelta(minutes=int(config.get("minimum_lead_minutes", 90)))
    candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if candidate <= now + lead:
        candidate += timedelta(days=1)

    occupied_local: set[str] = set()
    for raw in occupied_publish_at:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(tz)
            occupied_local.add(dt.strftime("%Y-%m-%d %H:%M"))
        except ValueError:
            continue

    result: list[datetime] = []
    while len(result) < count:
        if candidate.strftime("%Y-%m-%d %H:%M") not in occupied_local:
            result.append(candidate)
        candidate += timedelta(days=1)
    return result


def to_utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
