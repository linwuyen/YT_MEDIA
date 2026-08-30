from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo


def performance_score(metrics: dict[str, Any]) -> float:
    """Return a 0..100 local optimization score from audience outcomes.

    This is deliberately a channel-local ranking score, not a claim about
    YouTube's recommendation formula. Retention and engaged viewing dominate;
    shares/comments/subscriber conversion provide smaller quality signals.
    """
    views = max(0.0, float(metrics.get("views") or 0))
    engaged = max(0.0, float(metrics.get("engagedViews") or views))
    avg_pct = max(0.0, float(metrics.get("averageViewPercentage") or 0))
    likes = max(0.0, float(metrics.get("likes") or 0))
    comments = max(0.0, float(metrics.get("comments") or 0))
    shares = max(0.0, float(metrics.get("shares") or 0))
    subs = max(0.0, float(metrics.get("subscribersGained") or 0))

    retention = min(avg_pct / 100.0, 1.25) / 1.25
    velocity = min(math.log1p(engaged) / math.log1p(10000.0), 1.0)
    interaction_rate = (likes + 2.0 * comments + 3.0 * shares) / max(engaged, 1.0)
    interaction = min(interaction_rate / 0.08, 1.0)
    subscriber = min((subs / max(engaged, 1.0)) / 0.01, 1.0)
    engaged_ratio = min(engaged / max(views, 1.0), 1.0)

    score = 100.0 * (
        0.40 * retention
        + 0.20 * velocity
        + 0.15 * interaction
        + 0.15 * subscriber
        + 0.10 * engaged_ratio
    )
    return round(max(0.0, min(score, 100.0)), 3)


def _window_snapshot(entry: dict[str, Any], preferred: Iterable[str] = ("7d", "72h", "24h")) -> dict[str, Any] | None:
    analytics = entry.get("analytics", {})
    if not isinstance(analytics, dict):
        return None
    for name in preferred:
        snapshot = analytics.get(name)
        if isinstance(snapshot, dict) and snapshot.get("score") is not None:
            return snapshot
    return None


def arm_statistics(
    state: dict[str, Any],
    assignment_key: str,
    arms: Iterable[str],
) -> dict[str, dict[str, float]]:
    result = {str(arm): {"count": 0.0, "mean": 50.0} for arm in arms}
    sums = {str(arm): 0.0 for arm in arms}
    files = state.get("files", {}) if isinstance(state, dict) else {}
    if not isinstance(files, dict):
        return result

    for entry in files.values():
        if not isinstance(entry, dict):
            continue
        arm = str(entry.get(assignment_key) or "")
        if arm not in result:
            continue
        snapshot = _window_snapshot(entry)
        if not snapshot:
            continue
        try:
            score = float(snapshot["score"])
        except (TypeError, ValueError, KeyError):
            continue
        result[arm]["count"] += 1.0
        sums[arm] += score

    for arm, row in result.items():
        if row["count"]:
            row["mean"] = round(sums[arm] / row["count"], 3)
    return result


def choose_arm(
    file_id: str,
    arms: Iterable[str],
    stats: dict[str, dict[str, float]] | None = None,
    *,
    salt: str,
    exploration: float = 0.35,
) -> str:
    """Deterministic weighted exploration using an exponential race.

    Better observed arms get more traffic, while under-sampled arms retain an
    exploration bonus. The Drive file ID makes retries stable.
    """
    arm_list = [str(x) for x in arms if str(x)]
    if not arm_list:
        raise ValueError("At least one strategy arm is required")
    stats = stats or {}
    total = sum(float(stats.get(arm, {}).get("count", 0.0)) for arm in arm_list)

    winner = arm_list[0]
    winner_key = float("inf")
    for arm in arm_list:
        row = stats.get(arm, {})
        count = float(row.get("count", 0.0))
        mean = float(row.get("mean", 50.0)) / 100.0
        bonus = exploration * math.sqrt(math.log(total + 2.0) / (count + 1.0))
        weight = max(0.05, mean + bonus)
        digest = hashlib.sha256(f"{salt}:{file_id}:{arm}".encode("utf-8")).digest()
        integer = int.from_bytes(digest[:8], "big")
        u = (integer + 1.0) / (2**64 + 1.0)
        key = -math.log(u) / weight
        if key < winner_key:
            winner_key = key
            winner = arm
    return winner


def metadata_config_for_arm(config: dict[str, Any], arm_id: str | None) -> dict[str, Any]:
    """Return a shallow config variant that makes an experiment arm stable.

    Each arm owns a search-term style, hook style, and rotating hashtag family;
    other wording still varies deterministically by Drive file ID.
    """
    if not arm_id:
        return config
    arm = next(
        (
            item for item in config.get("metadata_arms", [])
            if isinstance(item, dict) and str(item.get("id")) == str(arm_id)
        ),
        None,
    )
    if not arm:
        return config

    result = dict(config)
    for key, index_key in (
        ("named_search_title_terms", "search_index"),
        ("generic_search_title_terms", "search_index"),
        ("title_hooks", "hook_index"),
        ("hashtag_rotation", "hashtag_index"),
    ):
        values = list(config.get(key, []))
        if values:
            index = int(arm.get(index_key, 0)) % len(values)
            result[key] = [values[index]]
    return result


def schedule_slots_for_times(
    config: dict[str, Any],
    times: list[str],
    occupied_publish_at: Iterable[str],
    now: datetime | None = None,
) -> list[datetime]:
    """Schedule at most one channel upload per local calendar day.

    Each pending video may carry a different learned publish-time arm. Existing
    scheduled uploads block their entire local date, preventing accidental
    same-day cannibalization during the experiment.
    """
    tz = ZoneInfo(str(config["timezone"]))
    now = now.astimezone(tz) if now else datetime.now(tz)
    lead = timedelta(minutes=int(config.get("minimum_lead_minutes", 90)))

    occupied_dates: set[str] = set()
    for raw in occupied_publish_at:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(tz)
            occupied_dates.add(dt.strftime("%Y-%m-%d"))
        except ValueError:
            continue

    result: list[datetime] = []
    cursor_date = now.date()
    for raw_time in times:
        hh, mm = (int(x) for x in str(raw_time).split(":", 1))
        while True:
            candidate = datetime(
                cursor_date.year,
                cursor_date.month,
                cursor_date.day,
                hh,
                mm,
                tzinfo=tz,
            )
            date_key = candidate.strftime("%Y-%m-%d")
            if date_key not in occupied_dates and candidate > now + lead:
                result.append(candidate)
                occupied_dates.add(date_key)
                cursor_date = cursor_date + timedelta(days=1)
                break
            cursor_date = cursor_date + timedelta(days=1)
    return result
