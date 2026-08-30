from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .core import load_config
from .google_api import build_drive, read_drive_state, write_drive_state
from .learning import active_experiment, score_dimensions
from .metadata_optimizer import build_editor, manage_token_path
from .strategy import arm_statistics

REPO_ROOT = Path(__file__).resolve().parents[2]
ANALYTICS_METRICS = (
    "views,engagedViews,estimatedMinutesWatched,averageViewDuration,"
    "averageViewPercentage,likes,comments,shares,subscribersGained"
)
PACIFIC = ZoneInfo("America/Los_Angeles")


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


def _data_api_metrics(youtube, video_id: str) -> dict[str, float]:
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

    return {
        "views": number("viewCount"),
        "likes": number("likeCount"),
        "comments": number("commentCount"),
    }


def _analytics_metrics(service, video_id: str, start: datetime, end: datetime) -> dict[str, float]:
    response = service.reports().query(
        ids="channel==MINE",
        startDate=start.astimezone(PACIFIC).strftime("%Y-%m-%d"),
        endDate=end.astimezone(PACIFIC).strftime("%Y-%m-%d"),
        metrics=ANALYTICS_METRICS,
        filters=f"video=={video_id}",
    ).execute()
    return _rows_to_metrics(response)


def _traffic_source_summary(service, video_id: str, start: datetime, end: datetime) -> dict[str, Any]:
    response = service.reports().query(
        ids="channel==MINE",
        startDate=start.astimezone(PACIFIC).strftime("%Y-%m-%d"),
        endDate=end.astimezone(PACIFIC).strftime("%Y-%m-%d"),
        metrics="views,engagedViews",
        dimensions="insightTrafficSourceType",
        filters=f"video=={video_id}",
    ).execute()
    headers = [column.get("name", "") for column in response.get("columnHeaders", [])]
    sources: dict[str, dict[str, float]] = {}
    total_views = 0.0
    total_engaged = 0.0
    for row in response.get("rows", []) or []:
        values = dict(zip(headers, row))
        source = str(values.get("insightTrafficSourceType") or "UNKNOWN")
        views = float(values.get("views") or 0)
        engaged = float(values.get("engagedViews") or 0)
        sources[source] = {"views": views, "engagedViews": engaged}
        total_views += views
        total_engaged += engaged
    shorts = sources.get("SHORTS", {})
    denominator = total_engaged or total_views or 1.0
    shorts_value = float(shorts.get("engagedViews") or shorts.get("views") or 0)
    return {
        "sources": sources,
        "shortsFeedShare": round(shorts_value / denominator, 6),
    }


def _retention_summary(service, video_id: str, start: datetime, end: datetime) -> dict[str, Any]:
    response = service.reports().query(
        ids="channel==MINE",
        startDate=start.astimezone(PACIFIC).strftime("%Y-%m-%d"),
        endDate=end.astimezone(PACIFIC).strftime("%Y-%m-%d"),
        metrics="audienceWatchRatio,relativeRetentionPerformance",
        dimensions="elapsedVideoTimeRatio",
        filters=f"video=={video_id}",
        sort="elapsedVideoTimeRatio",
    ).execute()
    headers = [column.get("name", "") for column in response.get("columnHeaders", [])]
    points: list[dict[str, float]] = []
    for row in response.get("rows", []) or []:
        values = dict(zip(headers, row))
        try:
            points.append({
                "ratio": float(values.get("elapsedVideoTimeRatio") or 0),
                "audienceWatchRatio": float(values.get("audienceWatchRatio") or 0),
                "relativeRetentionPerformance": float(values.get("relativeRetentionPerformance") or 0),
            })
        except (TypeError, ValueError):
            continue
    if not points:
        return {}

    def nearest(target: float) -> dict[str, float]:
        return min(points, key=lambda point: abs(point["ratio"] - target))

    checkpoints = {
        name: nearest(value)
        for name, value in (("1pct", 0.01), ("10pct", 0.10), ("25pct", 0.25), ("50pct", 0.50), ("75pct", 0.75), ("100pct", 1.0))
    }
    return {
        "points": len(points),
        "checkpoints": checkpoints,
        "first10Drop": round(
            max(0.0, checkpoints["1pct"]["audienceWatchRatio"] - checkpoints["10pct"]["audienceWatchRatio"]),
            6,
        ),
        "halfwayAudienceWatchRatio": round(checkpoints["50pct"]["audienceWatchRatio"], 6),
        "completionAudienceWatchRatio": round(checkpoints["100pct"]["audienceWatchRatio"], 6),
    }


def _exact_windows(config: dict[str, Any]) -> list[tuple[str, float]]:
    raw = config.get("exact_snapshot_hours", {"1h": 1, "6h": 6, "24h": 24, "72h": 72, "7d": 168})
    return [(str(name), float(hours)) for name, hours in raw.items()]


def _analytics_windows(config: dict[str, Any]) -> list[tuple[str, float]]:
    raw = config.get("analytics_windows_hours", {"24h": 24, "72h": 72, "7d": 168})
    return [(str(name), float(hours)) for name, hours in raw.items()]


def _capture_exact(youtube, entry: dict[str, Any], video_id: str, publish_at: datetime, now: datetime, config: dict[str, Any]) -> int:
    exact = entry.setdefault("exact", {})
    touched = 0
    age_hours = max(0.0, (now - publish_at).total_seconds() / 3600.0)
    for name, threshold in _exact_windows(config):
        if age_hours < threshold or name in exact:
            continue
        metrics = _data_api_metrics(youtube, video_id)
        if not metrics:
            continue
        exact[name] = {
            **metrics,
            "source": "youtube_data_api",
            "time_basis": "cumulative_at_capture",
            "target_age_hours": threshold,
            "actual_age_hours": round(age_hours, 3),
            "captured_at": now.isoformat(),
            "training_eligible": False,
        }
        touched += 1
    return touched


def _capture_learning_window(service, entry: dict[str, Any], video_id: str, publish_at: datetime, now: datetime, name: str, threshold: float) -> bool:
    age_hours = max(0.0, (now - publish_at).total_seconds() / 3600.0)
    if age_hours < threshold:
        return False
    analytics_state = entry.setdefault("analytics", {})
    existing = analytics_state.get(name)
    if isinstance(existing, dict) and existing.get("source") == "youtube_analytics":
        return False

    end = min(now, publish_at + timedelta(hours=threshold))
    metrics = _analytics_metrics(service, video_id, publish_at, end)
    if not metrics:
        return False

    traffic: dict[str, Any] = {}
    retention: dict[str, Any] = {}
    try:
        traffic = _traffic_source_summary(service, video_id, publish_at, end)
        metrics["shortsFeedShare"] = float(traffic.get("shortsFeedShare") or 0)
    except Exception:
        pass
    try:
        retention = _retention_summary(service, video_id, publish_at, end)
    except Exception:
        pass

    scores = score_dimensions(metrics)
    analytics_state[name] = {
        **metrics,
        "score": scores["total"],
        "scores": scores,
        "traffic": traffic,
        "retention_curve": retention,
        "source": "youtube_analytics",
        "time_basis": "calendar_date_query",
        "target_age_hours": threshold,
        "actual_age_hours": round(age_hours, 3),
        "captured_at": now.isoformat(),
        "training_eligible": True,
    }
    return True


def collect(config: dict[str, Any]) -> int:
    if not manage_token_path().exists():
        print("Analytics/metadata management token 尚未設定；略過 feedback loop，不影響正常上傳。")
        return 0

    try:
        youtube, creds, channel = build_editor(config, interactive=False)
    except Exception as exc:
        print(json.dumps({"event": "analytics_skipped", "reason": str(exc)}, ensure_ascii=False))
        return 0

    try:
        analytics = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
    except Exception:
        analytics = None

    drive, _ = build_drive(False)
    root_id = str(config["drive_root_folder_id"])
    state, state_file_id = read_drive_state(drive, root_id)
    files_state = state.setdefault("files", {})
    now = datetime.now(timezone.utc)
    exact_added = 0
    learning_added = 0
    analytics_error = ""

    for entry in files_state.values():
        if not isinstance(entry, dict):
            continue
        video_id = str(entry.get("youtube_video_id") or "")
        publish_at = _parse_publish_at(entry)
        if not video_id or not publish_at or publish_at > now:
            continue

        try:
            exact_added += _capture_exact(youtube, entry, video_id, publish_at, now, config)
        except Exception as exc:
            entry["exact_snapshot_warning"] = str(exc)[:500]

        if analytics is None:
            continue
        for name, threshold in _analytics_windows(config):
            try:
                if _capture_learning_window(analytics, entry, video_id, publish_at, now, name, threshold):
                    learning_added += 1
            except HttpError as exc:
                analytics_error = str(exc)
                break
            except Exception as exc:
                analytics_error = str(exc)
                break

    metadata_arms = [str(x.get("id")) for x in config.get("metadata_arms", []) if isinstance(x, dict) and x.get("id")]
    publish_arms = [str(x) for x in config.get("publish_time_arms", []) if str(x)]
    thumbnail_arms = [str(x) for x in config.get("thumbnail_arms", []) if str(x)]
    strategy = state.setdefault("strategy", {})
    strategy["metadata"] = arm_statistics(state, "metadata_arm", metadata_arms)
    strategy["publish_time"] = arm_statistics(state, "publish_time_arm", publish_arms)
    strategy["thumbnail"] = arm_statistics(state, "thumbnail_arm", thumbnail_arms)
    strategy["experiment"] = active_experiment(state, config)
    strategy["updated_at"] = now.isoformat()
    strategy["score_formula"] = "reach20_retention45_engagement20_conversion15"
    strategy["exact_backend"] = "youtube_data_api"
    strategy["analytics_backend"] = "youtube_analytics" if analytics is not None and not analytics_error else "unavailable"
    if analytics_error:
        strategy["analytics_warning"] = analytics_error[:1000]

    state_file_id = write_drive_state(drive, root_id, state, file_id=state_file_id)
    print(json.dumps({
        "ok": True,
        "channel": channel,
        "exact_snapshots_added": exact_added,
        "learning_snapshots_added": learning_added,
        "analytics_backend": strategy["analytics_backend"],
        "experiment": strategy["experiment"],
        "metadata_arm_stats": strategy["metadata"],
        "publish_time_stats": strategy["publish_time"],
        "thumbnail_arm_stats": strategy["thumbnail"],
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
