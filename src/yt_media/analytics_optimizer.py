from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .core import load_config
from .google_api import build_drive, read_drive_state, write_drive_state
from .metadata_optimizer import build_editor
from .strategy import arm_statistics, performance_score

REPO_ROOT = Path(__file__).resolve().parents[2]
ANALYTICS_METRICS = (
    "views,engagedViews,estimatedMinutesWatched,averageViewDuration,"
    "averageViewPercentage,likes,comments,shares,subscribersGained"
)


def _parse_publish_at(entry: dict[str, Any]) -> datetime | None:
    raw = entry.get("publish_at_local")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _rows_to_metrics(response: dict[str, Any]) -> dict[str, float]:
    headers = [column.get("name", "") for column in response.get("columnHeaders", [])]
    rows = response.get("rows", [])
    values = rows[0] if rows else []
    result: dict[str, float] = {}
    for name, value in zip(headers, values):
        try:
            result[str(name)] = float(value)
        except (TypeError, ValueError):
            result[str(name)] = 0.0
    return result


def _analytics_metrics(service, video_id: str, start: datetime, end: datetime) -> dict[str, float]:
    response = service.reports().query(
        ids="channel==MINE",
        startDate=start.strftime("%Y-%m-%d"),
        endDate=end.strftime("%Y-%m-%d"),
        metrics=ANALYTICS_METRICS,
        filters=f"video=={video_id}",
    ).execute()
    return _rows_to_metrics(response)


def _data_api_fallback(youtube, video_id: str) -> dict[str, float]:
    response = youtube.videos().list(part="statistics", id=video_id).execute()
    items = response.get("items", [])
    if not items:
        return {}
    stats = items[0].get("statistics", {})
    def number(name: str) -> float:
        try:
            return float(stats.get(name) or 0)
        except (TypeError, ValueError):
            return 0.0
    views = number("viewCount")
    return {
        "views": views,
        "engagedViews": views,
        "likes": number("likeCount"),
        "comments": number("commentCount"),
        "shares": 0.0,
        "subscribersGained": 0.0,
        "averageViewDuration": 0.0,
        "averageViewPercentage": 0.0,
    }


def _window_definitions(config: dict[str, Any]) -> list[tuple[str, timedelta]]:
    raw = config.get("analytics_windows_hours", {"24h": 24, "72h": 72, "7d": 168})
    return [(str(name), timedelta(hours=float(hours))) for name, hours in raw.items()]


def collect(config: dict[str, Any]) -> int:
    if not Path(str(config.get("drive_root_folder_id", ""))):
        raise RuntimeError("drive_root_folder_id is required")

    youtube, creds, channel = build_editor(config, interactive=False)
    try:
        analytics = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
    except Exception:
        analytics = None

    drive, _ = build_drive(False)
    root_id = str(config["drive_root_folder_id"])
    state, state_file_id = read_drive_state(drive, root_id)
    files_state = state.setdefault("files", {})
    now = datetime.now(timezone.utc)
    windows = _window_definitions(config)
    touched = 0
    analytics_available = analytics is not None
    analytics_error = ""

    for entry in files_state.values():
        if not isinstance(entry, dict):
            continue
        video_id = entry.get("youtube_video_id")
        publish_at = _parse_publish_at(entry)
        if not video_id or not publish_at or publish_at > now:
            continue

        analytics_state = entry.setdefault("analytics", {})
        for window_name, window_age in windows:
            if now < publish_at + window_age:
                continue
            existing = analytics_state.get(window_name)
            # Once a window has been captured after a grace period, keep it stable
            # so arm comparisons use roughly equal-age observations.
            if isinstance(existing, dict) and existing.get("captured_at"):
                continue

            metrics: dict[str, float] = {}
            source = "youtube_analytics"
            if analytics_available and analytics is not None:
                try:
                    metrics = _analytics_metrics(
                        analytics,
                        str(video_id),
                        publish_at,
                        min(now, publish_at + window_age),
                    )
                except HttpError as exc:
                    analytics_available = False
                    analytics_error = str(exc)
                    metrics = _data_api_fallback(youtube, str(video_id))
                    source = "data_api_fallback"
                except Exception as exc:
                    analytics_available = False
                    analytics_error = str(exc)
                    metrics = _data_api_fallback(youtube, str(video_id))
                    source = "data_api_fallback"
            else:
                metrics = _data_api_fallback(youtube, str(video_id))
                source = "data_api_fallback"

            if not metrics:
                continue
            snapshot = {
                **metrics,
                "score": performance_score(metrics),
                "source": source,
                "captured_at": now.isoformat(),
                "window": window_name,
            }
            analytics_state[window_name] = snapshot
            touched += 1
            print(json.dumps({
                "event": "analytics_snapshot",
                "youtube_video_id": video_id,
                "window": window_name,
                "score": snapshot["score"],
                "source": source,
            }, ensure_ascii=False))

    metadata_arms = [str(x.get("id")) for x in config.get("metadata_arms", []) if isinstance(x, dict) and x.get("id")]
    publish_arms = [str(x) for x in config.get("publish_time_arms", []) if str(x)]
    strategy = state.setdefault("strategy", {})
    strategy["metadata"] = arm_statistics(state, "metadata_arm", metadata_arms)
    strategy["publish_time"] = arm_statistics(state, "publish_time_arm", publish_arms)
    strategy["updated_at"] = now.isoformat()
    strategy["score_formula"] = "retention40_velocity20_interaction15_subscriber15_engaged10"
    strategy["analytics_backend"] = "youtube_analytics" if analytics_available else "data_api_fallback"
    if analytics_error:
        strategy["analytics_warning"] = analytics_error[:1000]

    state_file_id = write_drive_state(drive, root_id, state, file_id=state_file_id)
    print(json.dumps({
        "ok": True,
        "channel": channel,
        "snapshots_added": touched,
        "analytics_backend": strategy["analytics_backend"],
        "metadata_arm_stats": strategy["metadata"],
        "publish_time_stats": strategy["publish_time"],
        "drive_state_file_id": state_file_id,
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="YT_MEDIA analytics feedback loop")
    parser.add_argument("command", choices=["collect"])
    args = parser.parse_args()
    config = load_config(REPO_ROOT)
    if args.command == "collect":
        return collect(config)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
