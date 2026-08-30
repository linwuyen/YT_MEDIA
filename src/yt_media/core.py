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


def _primary_video_stream(media_info: dict[str, Any]) -> dict[str, Any]:
    try:
        return next(s for s in media_info.get("streams", []) if s.get("codec_type") == "video")
    except StopIteration:
        return {}


def _parse_rate(value: Any) -> float:
    if value in (None, "", "0/0"):
        return 0.0
    text = str(value)
    if "/" in text:
        left, right = text.split("/", 1)
        try:
            denominator = float(right)
            return float(left) / denominator if denominator else 0.0
        except ValueError:
            return 0.0
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def media_traits(media_info: dict[str, Any]) -> dict[str, Any]:
    video = _primary_video_stream(media_info)
    try:
        duration = float(media_info.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    fps = _parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    vertical = bool(width and height and height > width)
    short = bool(vertical and 0 < duration <= 180)
    long_edge = max(width, height)
    short_edge = min(width, height)

    if long_edge >= 3840 and short_edge >= 2160:
        quality = "4K"
    elif long_edge >= 2160 and short_edge >= 1080:
        quality = "高畫質"
    else:
        quality = ""

    fps_label = "60fps" if fps >= 50 else ("30fps" if fps >= 25 else "")
    return {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "vertical": vertical,
        "short": short,
        "quality": quality,
        "fps_label": fps_label,
    }


def is_short(media_info: dict[str, Any]) -> bool:
    return bool(media_traits(media_info)["short"])


def _pick(values: list[str], digest: int, salt: int = 0) -> str:
    if not values:
        return ""
    shifted = digest >> (salt * 5)
    return values[shifted % len(values)]


def _clean_title(parts: Iterable[str]) -> str:
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return re.sub(r"\s+", " ", text).strip()


def make_metadata(item: VideoItem, media_info: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    person = detect_person(item.name, config.get("known_people", []))
    date_text = parse_recording_date(item.name)
    digest = int(hashlib.sha256(item.file_id.encode("utf-8")).hexdigest()[:16], 16)
    traits = media_traits(media_info)

    if person:
        search_terms = [
            value.format(person=person)
            for value in config.get("named_search_title_terms", ["{person} 啦啦隊應援直拍"])
        ]
    else:
        search_terms = list(config.get("generic_search_title_terms", ["啦啦隊應援直拍"]))

    hooks = list(config.get("title_hooks", ["球場現場氣氛實錄"]))
    search_term = _pick(search_terms, digest, 0)
    hook = _pick(hooks, digest, 2)

    tech = " ".join(bit for bit in (traits["quality"], traits["fps_label"]) if bit)
    title_left = _clean_title([search_term, tech])
    accent = _pick(list(config.get("title_accents", ["", "", "🔥", "✨"])), digest, 4)
    title = f"{title_left}｜{hook}{accent}".strip()[:100].rstrip()

    duration = traits["duration"]
    duration_text = f"{max(1, round(duration))} 秒" if duration else ""
    subject = f"{person}啦啦隊應援" if person else "球場啦啦隊應援"
    media_words = _clean_title([
        traits["quality"],
        traits["fps_label"],
        "直式直拍" if traits["vertical"] else "現場直拍",
    ])
    first_line = _clean_title([date_text, subject, media_words])
    first_line += f"，{duration_text}現場片段。" if duration_text else "。"

    second_lines = list(config.get(
        "description_second_lines",
        [
            "保留現場聲音與應援氣氛，更多球場直拍持續更新。",
            "高畫質記錄球場應援現場，喜歡這類直拍可以訂閱追蹤。",
            "現場原音與應援氣氛完整保留，更多片段會持續上線。",
        ],
    ))
    second_line = _pick(second_lines, digest, 3)

    hashtag_candidates = [person, "啦啦隊"] if person else ["啦啦隊"]
    rotating = list(config.get(
        "hashtag_rotation",
        ["球場應援", "應援直拍", "啦啦隊女孩", "Cheerleader", "現場直拍", "4K直拍"],
    ))
    hashtag_candidates.append(_pick(rotating, digest, 5))
    if traits["short"]:
        hashtag_candidates.append("Shorts")
    elif traits["quality"] == "4K":
        hashtag_candidates.append("4K")

    hashtags: list[str] = []
    limit = int(config.get("max_description_hashtags", 3))
    for tag in hashtag_candidates:
        if not tag:
            continue
        clean = str(tag).lstrip("#").replace(" ", "")
        if clean and clean not in hashtags:
            hashtags.append(clean)
        if len(hashtags) >= limit:
            break

    hashtag_line = " ".join(f"#{tag}" for tag in hashtags)
    description = (
        f"{first_line}\n"
        f"{second_line}\n\n"
        "你最喜歡哪個瞬間？留言告訴我👇\n"
        f"{hashtag_line}"
    )

    tags = list(dict.fromkeys([
        *([person] if person else []),
        "啦啦隊",
        "球場應援",
        "應援直拍",
        "Cheerleader",
        *(["Shorts"] if traits["short"] else []),
        *(["4K"] if traits["quality"] == "4K" else []),
    ]))

    return {
        "title": title,
        "description": description,
        "tags": tags[:15],
        "hashtags": hashtags,
        "person": person,
        "recording_date": date_text,
        "is_short": traits["short"],
        "metadata_version": int(config.get("metadata_version", 2)),
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
