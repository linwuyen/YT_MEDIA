from datetime import datetime
from zoneinfo import ZoneInfo

from yt_media.core import VideoItem, detect_person, make_metadata, parse_recording_date, schedule_slots


def sample_item(name="李珠垠_20260728_001.mp4"):
    return VideoItem(
        file_id="abc123",
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
        "known_people": ["李珠垠", "李雅英"],
        "named_title_templates": ["{person}應援魅力全開🔥｜球場啦啦隊直拍 4K"],
        "generic_title_templates": ["球場應援魅力全開🔥｜啦啦隊直拍 4K"],
        "hashtags": ["啦啦隊", "Shorts"],
    }


def test_person_and_date_detection():
    assert detect_person("李珠垠_001.mp4", ["李珠垠", "李雅英"]) == "李珠垠"
    assert parse_recording_date("video_20260728_193356.mp4") == "2026.07.28"


def test_metadata_adds_person_and_shorts():
    media = {"format": {"duration": "51.6"}, "streams": [{"codec_type": "video", "width": 2160, "height": 3840}]}
    meta = make_metadata(sample_item(), media, config())
    assert meta["person"] == "李珠垠"
    assert "李珠垠" in meta["title"]
    assert "#Shorts" in meta["title"]


def test_schedule_skips_today_when_too_late_and_occupied_day():
    cfg = config()
    tz = ZoneInfo("Asia/Taipei")
    now = datetime(2026, 8, 9, 18, 0, tzinfo=tz)
    occupied = ["2026-08-10T10:30:00Z"]
    slots = schedule_slots(cfg, 2, occupied, now=now)
    assert slots[0].strftime("%Y-%m-%d %H:%M") == "2026-08-11 18:30"
    assert slots[1].strftime("%Y-%m-%d %H:%M") == "2026-08-12 18:30"
