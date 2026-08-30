from datetime import datetime
from zoneinfo import ZoneInfo

from yt_media.learning import (
    active_experiment,
    build_context,
    champion_arm,
    contextual_arm_statistics,
    score_dimensions,
)
from yt_media.qc import fingerprint_distance, find_duplicate
from yt_media.strategy import (
    arm_statistics,
    choose_arm,
    metadata_config_for_arm,
    performance_score,
    schedule_slots_for_times,
)


def test_performance_score_rewards_retention_and_engagement():
    weak = performance_score({
        "views": 1000,
        "engagedViews": 500,
        "averageViewPercentage": 30,
        "likes": 5,
        "comments": 0,
        "shares": 0,
        "subscribersGained": 0,
    })
    strong = performance_score({
        "views": 1000,
        "engagedViews": 900,
        "averageViewPercentage": 92,
        "likes": 80,
        "comments": 10,
        "shares": 15,
        "subscribersGained": 8,
    })
    assert strong > weak
    assert 0 <= weak <= 100
    assert 0 <= strong <= 100
    dimensions = score_dimensions({
        "views": 1000,
        "engagedViews": 900,
        "averageViewPercentage": 92,
        "likes": 80,
        "comments": 10,
        "shares": 15,
        "subscribersGained": 8,
    })
    assert set(dimensions) == {"reach", "retention", "engagement", "conversion", "total"}


def test_arm_statistics_prefers_mature_learning_window_and_ignores_fallback():
    state = {
        "files": {
            "a": {
                "metadata_arm": "direct",
                "analytics": {
                    "24h": {"score": 30, "source": "youtube_analytics"},
                    "72h": {"score": 70, "source": "youtube_analytics"},
                },
            },
            "b": {
                "metadata_arm": "direct",
                "analytics": {"7d": {"score": 90, "source": "youtube_analytics"}},
            },
            "fallback": {
                "metadata_arm": "direct",
                "analytics": {"7d": {"score": 100, "source": "data_api_fallback"}},
            },
        }
    }
    stats = arm_statistics(state, "metadata_arm", ["direct", "live"])
    assert stats["direct"]["count"] == 2
    assert stats["direct"]["mean"] == 80
    assert stats["live"]["count"] == 0


def test_contextual_stats_weight_matching_context_more():
    target = {
        "person": "卡洛琳",
        "duration_bucket": "16-30",
        "quality": "4K",
        "fps_bucket": "60",
        "orientation": "vertical",
        "opening": "active",
        "visual_change": "high",
        "audio_loudness": "loud",
        "silence_ratio": "low",
        "scene_density": "medium",
        "weekday_group": "weekend",
    }
    state = {
        "files": {
            "similar": {
                "metadata_arm": "a",
                "context": dict(target),
                "analytics": {"72h": {"score": 90, "source": "youtube_analytics"}},
            },
            "different": {
                "metadata_arm": "a",
                "context": {
                    "person": "generic",
                    "duration_bucket": "91-180",
                    "quality": "unknown",
                    "fps_bucket": "30",
                    "orientation": "horizontal",
                    "opening": "static",
                    "visual_change": "low",
                    "audio_loudness": "quiet",
                    "silence_ratio": "high",
                    "scene_density": "low",
                    "weekday_group": "weekday",
                },
                "analytics": {"72h": {"score": 10, "source": "youtube_analytics"}},
            },
        }
    }
    stats = contextual_arm_statistics(state, "metadata_arm", ["a", "b"], target)
    assert stats["a"]["contextual_mean"] > stats["a"]["mean"]
    assert stats["b"]["count"] == 0


def test_contextual_champion_prefers_contextual_mean():
    stats = {
        "global": {"count": 10, "mean": 80, "effective_count": 3, "contextual_mean": 45},
        "matched": {"count": 6, "mean": 65, "effective_count": 5, "contextual_mean": 88},
    }
    assert champion_arm(stats, "global") == "matched"


def test_staged_experiment_only_advances_after_mature_samples():
    config = {
        "experiment_plan": [
            {"name": "metadata", "assignment_key": "metadata_arm", "mature_window": "72h", "min_mature_samples": 2},
            {"name": "publish_time", "assignment_key": "publish_time_arm", "mature_window": "72h", "min_mature_samples": 1},
        ]
    }
    state = {"files": {}}
    assert active_experiment(state, config)["name"] == "metadata"
    state["files"] = {
        "a": {"metadata_arm": "x", "analytics": {"72h": {"score": 50, "source": "youtube_analytics"}}},
        "b": {"metadata_arm": "y", "analytics": {"72h": {"score": 60, "source": "youtube_analytics"}}},
    }
    assert active_experiment(state, config)["name"] == "publish_time"


def test_context_builder_buckets_opening_duration_audio_and_motion():
    context = build_context(
        person="卡洛琳",
        duration=28.0,
        quality="4K",
        fps=59.94,
        vertical=True,
        first_second={"mean_brightness": 80, "mean_motion_hamming": 8, "available": True},
        content_features={
            "sampled_visual_change_index": 15,
            "audio": {"mean_volume_db": -12, "silence_ratio": 0.02},
            "scene": {"scene_changes_per_minute": 5},
        },
        publish_at=datetime(2026, 9, 5, 18, 30, tzinfo=ZoneInfo("Asia/Taipei")),
    )
    assert context["duration_bucket"] == "16-30"
    assert context["opening"] == "active"
    assert context["visual_change"] == "high"
    assert context["audio_loudness"] == "loud"
    assert context["silence_ratio"] == "low"
    assert context["scene_density"] == "medium"
    assert context["weekday_group"] == "weekend"


def test_choose_arm_is_retry_stable():
    stats = {
        "a": {"count": 20, "mean": 75},
        "b": {"count": 20, "mean": 45},
    }
    first = choose_arm("drive-file-1", ["a", "b"], stats, salt="metadata")
    second = choose_arm("drive-file-1", ["a", "b"], stats, salt="metadata")
    assert first == second


def test_metadata_arm_pins_style_family():
    config = {
        "metadata_arms": [{"id": "live", "search_index": 1, "hook_index": 1, "hashtag_index": 1}],
        "named_search_title_terms": ["A {person}", "B {person}"],
        "generic_search_title_terms": ["GA", "GB"],
        "title_hooks": ["HA", "HB"],
        "hashtag_rotation": ["TA", "TB"],
    }
    arm = metadata_config_for_arm(config, "live")
    assert arm["named_search_title_terms"] == ["B {person}"]
    assert arm["generic_search_title_terms"] == ["GB"]
    assert arm["title_hooks"] == ["HB"]
    assert arm["hashtag_rotation"] == ["TB"]


def test_adaptive_schedule_keeps_one_video_per_day():
    config = {"timezone": "Asia/Taipei", "minimum_lead_minutes": 90}
    tz = ZoneInfo("Asia/Taipei")
    now = datetime(2026, 9, 1, 12, 0, tzinfo=tz)
    occupied = ["2026-09-02T10:30:00Z"]
    slots = schedule_slots_for_times(config, ["17:30", "21:30", "19:30"], occupied, now=now)
    assert [x.strftime("%Y-%m-%d") for x in slots] == ["2026-09-01", "2026-09-03", "2026-09-04"]
    assert [x.strftime("%H:%M") for x in slots] == ["17:30", "21:30", "19:30"]


def test_duplicate_fingerprint_is_strict():
    first = {"duration": 30.0, "hashes": ["0000000000000000", "ffffffffffffffff"]}
    near = {"duration": 30.3, "hashes": ["0000000000000001", "fffffffffffffffe"]}
    far = {"duration": 30.3, "hashes": ["ffffffffffffffff", "0000000000000000"]}
    assert fingerprint_distance(first, near) == 1.0
    state = {"old": {"media_fingerprint": first, "youtube_video_id": "yt-old"}}
    duplicate = find_duplicate(near, state, "new", hamming_threshold=2.0)
    assert duplicate and duplicate["youtube_video_id"] == "yt-old"
    assert find_duplicate(far, state, "new", hamming_threshold=2.0) is None
