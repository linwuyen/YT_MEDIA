from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from yt_media.core import (
    VideoItem,
    detect_person,
    make_metadata,
    merge_state_documents,
    parse_recording_date,
    schedule_slots,
)


def sample_item(name="李珠垠_20260728_001.mp4", file_id="abc123"):
    return VideoItem(
        file_id=file_id,
        name=name,
        mime_type="video/mp4",
        size=100,
        parent_id="p1",
        parent_name="01_優先上傳",
        done_parent_id="date1",
        priority=True,
        modified_time="2026-07-28T00:00:00Z",
    )


def config():
    return {
        "timezone": "Asia/Taipei",
        "publish_time": "18:30",
        "minimum_lead_minutes": 90,
        "metadata_version": 2,
        "max_description_hashtags": 3,
        "known_people": ["李珠垠", "李雅英"],
        "named_search_title_terms": [
            "{person} 啦啦隊應援直拍",
            "{person} 球場應援直拍",
        ],
        "generic_search_title_terms": [
            "啦啦隊應援直拍",
            "球場啦啦隊直拍",
        ],
        "title_hooks": [
            "現場氣氛實錄",
            "應援精彩片段",
            "球場現場紀錄",
        ],
        "title_accents": ["", "🔥"],
        "description_second_lines": [
            "保留現場聲音與應援氣氛。",
            "現場原音完整保留。",
        ],
        "hashtag_rotation": ["球場應援", "應援直拍", "Cheerleader"],
    }


def short_media():
    return {
        "format": {"duration": "51.6"},
        "streams": [{
            "codec_type": "video",
            "width": 2160,
            "height": 3840,
            "avg_frame_rate": "60/1",
        }],
    }


def test_person_and_date_detection():
    assert detect_person("李珠垠_001.mp4", ["李珠垠", "李雅英"]) == "李珠垠"
    assert parse_recording_date("video_20260728_193356.mp4") == "2026.07.28"


def test_metadata_puts_search_terms_first_and_keeps_hashtags_tight():
    meta = make_metadata(sample_item(), short_media(), config())
    assert meta["person"] == "李珠垠"
    assert meta["title"].startswith("李珠垠")
    assert "4K" in meta["title"]
    assert "60fps" in meta["title"]
    assert "#Shorts" not in meta["title"]
    assert meta["is_short"] is True
    assert len(meta["hashtags"]) == 3
    assert "#李珠垠" in meta["description"]
    assert "#Shorts" in meta["description"]
    assert meta["metadata_version"] == 2


def test_metadata_is_deterministic_but_varies_by_drive_file():
    first = make_metadata(sample_item(name="video_20260813_220000.mp4", file_id="drive-a"), short_media(), config())
    again = make_metadata(sample_item(name="video_20260813_220000.mp4", file_id="drive-a"), short_media(), config())
    second = make_metadata(sample_item(name="video_20260813_220100.mp4", file_id="drive-b"), short_media(), config())
    assert first == again
    assert (first["title"], first["hashtags"]) != (second["title"], second["hashtags"])


def test_schedule_skips_today_when_too_late_and_occupied_day():
    cfg = config()
    tz = ZoneInfo("Asia/Taipei")
    now = datetime(2026, 8, 9, 18, 0, tzinfo=tz)
    occupied = ["2026-08-10T10:30:00Z"]
    slots = schedule_slots(cfg, 2, occupied, now=now)
    assert slots[0].strftime("%Y-%m-%d %H:%M") == "2026-08-11 18:30"
    assert slots[1].strftime("%Y-%m-%d %H:%M") == "2026-08-12 18:30"


def test_state_merge_preserves_uploaded_id_and_done_state():
    local = {
        "files": {
            "drive-1": {"youtube_video_id": "yt-1", "status": "uploaded", "moved": False},
            "drive-2": {"youtube_video_id": "yt-2", "status": "done", "moved": True},
        }
    }
    remote = {
        "files": {
            "drive-1": {"youtube_video_id": "yt-1", "status": "processing", "moved": False},
            "drive-3": {"youtube_video_id": "yt-3", "status": "done", "moved": True},
        }
    }
    merged = merge_state_documents(local, remote)
    assert merged["files"]["drive-1"]["youtube_video_id"] == "yt-1"
    assert merged["files"]["drive-2"]["moved"] is True
    assert merged["files"]["drive-3"]["moved"] is True


def test_state_merge_rejects_conflicting_youtube_ids():
    local = {"files": {"drive-1": {"youtube_video_id": "yt-a"}}}
    remote = {"files": {"drive-1": {"youtube_video_id": "yt-b"}}}
    with pytest.raises(RuntimeError, match="State conflict"):
        merge_state_documents(local, remote)
