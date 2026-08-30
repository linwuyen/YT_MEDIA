from datetime import datetime
from zoneinfo import ZoneInfo

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


def test_arm_statistics_prefers_mature_window():
    state = {
        "files": {
            "a": {
                "metadata_arm": "direct",
                "analytics": {"24h": {"score": 30}, "72h": {"score": 70}},
            },
            "b": {
                "metadata_arm": "direct",
                "analytics": {"7d": {"score": 90}},
            },
        }
    }
    stats = arm_statistics(state, "metadata_arm", ["direct", "live"])
    assert stats["direct"]["count"] == 2
    assert stats["direct"]["mean"] == 80
    assert stats["live"]["count"] == 0


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
    occupied = ["2026-09-02T10:30:00Z"]  # 18:30 local, blocks Sep 2 entirely
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
