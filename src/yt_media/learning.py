from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable


LEARNING_SOURCES = {"youtube_analytics"}


def score_dimensions(metrics: dict[str, Any]) -> dict[str, float]:
    """Split audience outcomes into interpretable 0..100 dimensions.

    This is a local channel optimization score, not YouTube's ranking formula.
    """
    views = max(0.0, float(metrics.get("views") or 0))
    engaged = max(0.0, float(metrics.get("engagedViews") or 0))
    avg_pct = max(0.0, float(metrics.get("averageViewPercentage") or 0))
    likes = max(0.0, float(metrics.get("likes") or 0))
    comments = max(0.0, float(metrics.get("comments") or 0))
    shares = max(0.0, float(metrics.get("shares") or 0))
    subs = max(0.0, float(metrics.get("subscribersGained") or 0))
    shorts_share = max(0.0, min(float(metrics.get("shortsFeedShare") or 0), 1.0))

    reach = min(math.log1p(max(engaged, views)) / math.log1p(10000.0), 1.0) * 100.0
    if shorts_share:
        reach = min(100.0, reach * 0.8 + shorts_share * 100.0 * 0.2)

    retention = min(avg_pct / 125.0, 1.0) * 100.0
    engagement_rate = (likes + 2.0 * comments + 3.0 * shares) / max(engaged, views, 1.0)
    engagement = min(engagement_rate / 0.08, 1.0) * 100.0
    conversion_rate = subs / max(engaged, views, 1.0)
    conversion = min(conversion_rate / 0.01, 1.0) * 100.0

    total = 0.20 * reach + 0.45 * retention + 0.20 * engagement + 0.15 * conversion
    return {
        "reach": round(reach, 3),
        "retention": round(retention, 3),
        "engagement": round(engagement, 3),
        "conversion": round(conversion, 3),
        "total": round(max(0.0, min(total, 100.0)), 3),
    }


def _bucket_duration(seconds: float) -> str:
    if seconds <= 0:
        return "unknown"
    if seconds <= 15:
        return "0-15"
    if seconds <= 30:
        return "16-30"
    if seconds <= 45:
        return "31-45"
    if seconds <= 60:
        return "46-60"
    if seconds <= 90:
        return "61-90"
    if seconds <= 180:
        return "91-180"
    return "180+"


def _weekday_group(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    return "weekend" if value.weekday() >= 5 else "weekday"


def build_context(
    *,
    person: str | None,
    duration: float,
    quality: str,
    fps: float,
    vertical: bool,
    first_second: dict[str, Any] | None,
    publish_at: datetime | None,
) -> dict[str, Any]:
    first_second = first_second or {}
    motion = float(first_second.get("motion_distance") or 0)
    brightness = float(first_second.get("brightness") or 0)
    if brightness and brightness < 10:
        opening = "dark"
    elif motion <= 1.5 and first_second:
        opening = "static"
    elif first_second:
        opening = "active"
    else:
        opening = "unknown"
    return {
        "person": person or "generic",
        "duration_bucket": _bucket_duration(duration),
        "quality": quality or "unknown",
        "fps_bucket": "60" if fps >= 50 else ("30" if fps >= 25 else "unknown"),
        "orientation": "vertical" if vertical else "horizontal",
        "opening": opening,
        "weekday_group": _weekday_group(publish_at),
    }


def context_similarity(left: dict[str, Any] | None, right: dict[str, Any] | None) -> float:
    if not left or not right:
        return 0.0
    weights = {
        "person": 2.0,
        "duration_bucket": 1.5,
        "quality": 0.75,
        "fps_bucket": 0.5,
        "orientation": 0.75,
        "opening": 1.5,
        "weekday_group": 0.75,
    }
    matched = 0.0
    total = 0.0
    for key, weight in weights.items():
        lv = left.get(key)
        rv = right.get(key)
        if not lv or not rv or lv == "unknown" or rv == "unknown":
            continue
        total += weight
        if lv == rv:
            matched += weight
    return matched / total if total else 0.0


def learning_snapshot(entry: dict[str, Any], preferred: Iterable[str] = ("7d", "72h", "24h")) -> dict[str, Any] | None:
    analytics = entry.get("analytics", {})
    if not isinstance(analytics, dict):
        return None
    for name in preferred:
        snapshot = analytics.get(name)
        if not isinstance(snapshot, dict):
            continue
        if snapshot.get("source") not in LEARNING_SOURCES:
            continue
        scores = snapshot.get("scores")
        if isinstance(scores, dict) and scores.get("total") is not None:
            return snapshot
        if snapshot.get("score") is not None:
            return snapshot
    return None


def contextual_arm_statistics(
    state: dict[str, Any],
    assignment_key: str,
    arms: Iterable[str],
    context: dict[str, Any] | None = None,
) -> dict[str, dict[str, float]]:
    arm_list = [str(x) for x in arms]
    rows: dict[str, list[tuple[float, float]]] = defaultdict(list)
    files = state.get("files", {}) if isinstance(state, dict) else {}
    if isinstance(files, dict):
        for entry in files.values():
            if not isinstance(entry, dict):
                continue
            arm = str(entry.get(assignment_key) or "")
            if arm not in arm_list:
                continue
            snapshot = learning_snapshot(entry)
            if not snapshot:
                continue
            scores = snapshot.get("scores") if isinstance(snapshot.get("scores"), dict) else {}
            score = scores.get("total", snapshot.get("score"))
            try:
                value = float(score)
            except (TypeError, ValueError):
                continue
            similarity = context_similarity(context, entry.get("context")) if context else 0.0
            weight = 1.0 if context is None else 0.25 + 0.75 * similarity
            rows[arm].append((value, weight))

    result: dict[str, dict[str, float]] = {}
    for arm in arm_list:
        samples = rows.get(arm, [])
        count = float(len(samples))
        effective = sum(weight for _, weight in samples)
        mean = sum(value for value, _ in samples) / count if count else 50.0
        contextual_mean = (
            sum(value * weight for value, weight in samples) / effective
            if effective else 50.0
        )
        result[arm] = {
            "count": count,
            "effective_count": round(effective, 3),
            "mean": round(mean, 3),
            "contextual_mean": round(contextual_mean, 3),
        }
    return result


def champion_arm(stats: dict[str, dict[str, float]], default: str) -> str:
    eligible = [
        (float(row.get("count", 0)), float(row.get("mean", 0)), arm)
        for arm, row in stats.items()
        if float(row.get("count", 0)) > 0
    ]
    if not eligible:
        return default
    eligible.sort(key=lambda item: (item[1], item[0], item[2]), reverse=True)
    return eligible[0][2]


def mature_sample_count(state: dict[str, Any], assignment_key: str, preferred_window: str = "72h") -> int:
    files = state.get("files", {}) if isinstance(state, dict) else {}
    total = 0
    if not isinstance(files, dict):
        return 0
    for entry in files.values():
        if not isinstance(entry, dict) or not entry.get(assignment_key):
            continue
        analytics = entry.get("analytics", {})
        if not isinstance(analytics, dict):
            continue
        snapshot = analytics.get(preferred_window) or analytics.get("7d")
        if isinstance(snapshot, dict) and snapshot.get("source") in LEARNING_SOURCES:
            total += 1
    return total


def active_experiment(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    plan = config.get("experiment_plan", [])
    if not isinstance(plan, list):
        plan = []
    for phase in plan:
        if not isinstance(phase, dict):
            continue
        key = str(phase.get("assignment_key") or "")
        if not key:
            continue
        minimum = int(phase.get("min_mature_samples", 24))
        window = str(phase.get("mature_window", "72h"))
        count = mature_sample_count(state, key, window)
        if count < minimum:
            return {
                "name": str(phase.get("name") or key),
                "assignment_key": key,
                "mature_window": window,
                "mature_samples": count,
                "target_samples": minimum,
            }
    return {
        "name": "exploit",
        "assignment_key": "",
        "mature_window": "7d",
        "mature_samples": 0,
        "target_samples": 0,
    }
