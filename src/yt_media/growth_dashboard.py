from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .core import load_config, runtime_dir
from .google_api import build_drive, read_drive_state
from .learning import active_experiment, learning_snapshot
from .strategy import arm_statistics

REPO_ROOT = Path(__file__).resolve().parents[2]


def _parse_publish(entry: dict[str, Any]) -> datetime | None:
    raw = entry.get("publish_at_local")
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _latest_exact(entry: dict[str, Any]) -> dict[str, Any] | None:
    exact = entry.get("exact", {})
    if not isinstance(exact, dict):
        return None
    for name in ("7d", "72h", "24h", "6h", "1h"):
        row = exact.get(name)
        if isinstance(row, dict):
            return row
    return None


def _leaderboard(stats: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    rows = [
        {
            "arm": arm,
            "count": int(float(row.get("count", 0))),
            "mean": round(float(row.get("mean", 50)), 3),
        }
        for arm, row in stats.items()
    ]
    return sorted(rows, key=lambda row: (row["mean"], row["count"]), reverse=True)


def _group_scores(files: dict[str, Any], context_key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for entry in files.values():
        if not isinstance(entry, dict):
            continue
        snapshot = learning_snapshot(entry)
        if not snapshot:
            continue
        scores = snapshot.get("scores") if isinstance(snapshot.get("scores"), dict) else {}
        value = scores.get("total", snapshot.get("score"))
        try:
            score = float(value)
        except (TypeError, ValueError):
            continue
        context = entry.get("context", {})
        if not isinstance(context, dict):
            continue
        group = str(context.get(context_key) or "unknown")
        grouped[group].append(score)
    result = []
    for group, values in grouped.items():
        result.append({
            "group": group,
            "count": len(values),
            "mean_score": round(sum(values) / len(values), 3),
        })
    return sorted(result, key=lambda row: (row["mean_score"], row["count"]), reverse=True)


def build_dashboard(state: dict[str, Any], config: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    files = state.get("files", {}) if isinstance(state, dict) else {}
    if not isinstance(files, dict):
        files = {}

    seven_days_ago = now - timedelta(days=7)
    recent = []
    total_views = total_likes = total_comments = 0.0
    subscriber_gain = 0.0
    scored: list[dict[str, Any]] = []

    for file_id, entry in files.items():
        if not isinstance(entry, dict):
            continue
        publish_at = _parse_publish(entry)
        if publish_at and seven_days_ago <= publish_at <= now:
            exact = _latest_exact(entry) or {}
            total_views += float(exact.get("views") or 0)
            total_likes += float(exact.get("likes") or 0)
            total_comments += float(exact.get("comments") or 0)
            snapshot = learning_snapshot(entry)
            if snapshot:
                subscriber_gain += float(snapshot.get("subscribersGained") or 0)
            recent.append(file_id)

        snapshot = learning_snapshot(entry)
        if snapshot:
            scores = snapshot.get("scores") if isinstance(snapshot.get("scores"), dict) else {}
            total = scores.get("total", snapshot.get("score"))
            try:
                score = float(total)
            except (TypeError, ValueError):
                continue
            scored.append({
                "drive_file_id": file_id,
                "youtube_video_id": entry.get("youtube_video_id"),
                "title": entry.get("title"),
                "score": round(score, 3),
                "scores": scores,
                "metadata_arm": entry.get("metadata_arm"),
                "publish_time_arm": entry.get("publish_time_arm"),
                "thumbnail_arm": entry.get("thumbnail_arm"),
                "context": entry.get("context"),
            })

    metadata_arms = [str(x.get("id")) for x in config.get("metadata_arms", []) if isinstance(x, dict) and x.get("id")]
    publish_arms = [str(x) for x in config.get("publish_time_arms", []) if str(x)]
    thumbnail_arms = [str(x) for x in config.get("thumbnail_arms", []) if str(x)]

    scored.sort(key=lambda row: row["score"], reverse=True)
    return {
        "generated_at": now.isoformat(),
        "channel": {
            "name": config.get("youtube_channel_name"),
            "id": config.get("youtube_channel_id"),
        },
        "active_experiment": active_experiment(state, config),
        "last_7_days": {
            "published_videos": len(recent),
            "cumulative_snapshot_views": int(total_views),
            "cumulative_snapshot_likes": int(total_likes),
            "cumulative_snapshot_comments": int(total_comments),
            "analytics_subscribers_gained": int(subscriber_gain),
        },
        "leaderboards": {
            "metadata": _leaderboard(arm_statistics(state, "metadata_arm", metadata_arms)),
            "publish_time": _leaderboard(arm_statistics(state, "publish_time_arm", publish_arms)),
            "thumbnail": _leaderboard(arm_statistics(state, "thumbnail_arm", thumbnail_arms)),
        },
        "content_correlations": {
            "opening": _group_scores(files, "opening"),
            "duration_bucket": _group_scores(files, "duration_bucket"),
            "visual_change": _group_scores(files, "visual_change"),
            "audio_loudness": _group_scores(files, "audio_loudness"),
            "silence_ratio": _group_scores(files, "silence_ratio"),
            "scene_density": _group_scores(files, "scene_density"),
            "person": _group_scores(files, "person"),
            "weekday_group": _group_scores(files, "weekday_group"),
        },
        "top_videos": scored[:10],
        "bottom_videos": list(reversed(scored[-10:])),
        "mature_scored_videos": len(scored),
    }


def render_markdown(dashboard: dict[str, Any]) -> str:
    lines = [
        "# YT_MEDIA Growth Dashboard",
        "",
        f"Generated: `{dashboard['generated_at']}`",
        "",
        "## Active experiment",
        "",
    ]
    experiment = dashboard.get("active_experiment", {})
    lines.append(
        f"**{experiment.get('name', 'unknown')}** — "
        f"{experiment.get('mature_samples', 0)}/{experiment.get('target_samples', 0)} mature samples"
    )
    lines += ["", "## Last 7 days", ""]
    recent = dashboard.get("last_7_days", {})
    for key, value in recent.items():
        lines.append(f"- {key}: **{value}**")

    lines += ["", "## Strategy leaderboards", ""]
    for name, rows in dashboard.get("leaderboards", {}).items():
        lines += [f"### {name}", "", "| Arm | Mature n | Mean score |", "|---|---:|---:|"]
        for row in rows:
            lines.append(f"| {row['arm']} | {row['count']} | {row['mean']:.3f} |")
        lines.append("")

    lines += ["## Top videos", "", "| Score | Title | Metadata | Time | Thumbnail |", "|---:|---|---|---|---|"]
    for row in dashboard.get("top_videos", []):
        title = str(row.get("title") or "").replace("|", "\\|")
        lines.append(
            f"| {row['score']:.3f} | {title} | {row.get('metadata_arm') or ''} | "
            f"{row.get('publish_time_arm') or ''} | {row.get('thumbnail_arm') or ''} |"
        )

    lines += ["", "## Content correlations", ""]
    for name, rows in dashboard.get("content_correlations", {}).items():
        lines += [f"### {name}", "", "| Group | n | Mean score |", "|---|---:|---:|"]
        for row in rows:
            lines.append(f"| {row['group']} | {row['count']} | {row['mean_score']:.3f} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def generate(config: dict[str, Any]) -> int:
    drive, _ = build_drive(False)
    state, _ = read_drive_state(drive, str(config["drive_root_folder_id"]))
    dashboard = build_dashboard(state, config)
    root = runtime_dir()
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "growth_dashboard.json"
    md_path = root / "growth_dashboard.md"
    json_path.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(dashboard), encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "json": str(json_path),
        "markdown": str(md_path),
        "active_experiment": dashboard["active_experiment"],
        "mature_scored_videos": dashboard["mature_scored_videos"],
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="YT_MEDIA growth dashboard")
    parser.add_argument("command", choices=["generate"])
    args = parser.parse_args()
    config = load_config(REPO_ROOT)
    if args.command == "generate":
        return generate(config)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
