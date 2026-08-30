from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .learning import learning_snapshot, score_dimensions


def performance_score(metrics: dict[str, Any]) -> float:
    """Compatibility wrapper for the channel-local total score."""
    return score_dimensions(metrics)["total"]


def arm_statistics(
    state: dict[str, Any],
    assignment_key: str,
    arms: Iterable[str],
    experiment_phase: str | None = None,
) -> dict[str, dict[str, float]]:
    result = {str(arm): {"count": 0.0, "mean": 50.0} for arm in arms}
    sums = {str(arm): 0.0 for arm in arms}
    files = state.get("files", {}) if isinstance(state, dict) else {}
    if not isinstance(files, dict):
        return result

    for entry in files.values():
        if not isinstance(entry, dict):
            continue
        if experiment_phase and str(entry.get("experiment_phase") or "") != experiment_phase:
            continue
        arm = str(entry.get(assignment_key) or "")
        if arm not in result:
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
        result[arm]["count"] += 1.0
        sums[arm] += value

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

    Contextual means and effective counts are used when present. Drive file ID
    keeps retries stable.
    """
    arm_list = [str(x) for x in arms if str(x)]
    if not arm_list:
        raise ValueError("At least one strategy arm is required")
    stats = stats or {}
    total = sum(
        float(stats.get(arm, {}).get("effective_count", stats.get(arm, {}).get("count", 0.0)))
        for arm in arm_list
    )

    winner = arm_list[0]
    winner_key = float("inf")
    for arm in arm_list:
        row = stats.get(arm, {})
        count = float(row.get("effective_count", row.get("count", 0.0)))
        mean = float(row.get("contextual_mean", row.get("mean", 50.0))) / 100.0
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
    """Schedule at most one channel upload per local calendar day."""
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
