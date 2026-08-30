from datetime import datetime, timezone

from yt_media.growth_dashboard import build_dashboard, render_markdown


def test_dashboard_reports_experiment_leaders_and_correlations():
    state = {
        "files": {
            "a": {
                "youtube_video_id": "yt-a",
                "title": "A",
                "metadata_arm": "direct",
                "publish_time_arm": "18:30",
                "thumbnail_arm": "best_frame",
                "publish_at_local": "2026-08-30T18:30:00+08:00",
                "context": {
                    "person": "generic",
                    "duration_bucket": "16-30",
                    "opening": "active",
                    "weekday_group": "weekend",
                },
                "exact": {"24h": {"views": 1000, "likes": 50, "comments": 4}},
                "analytics": {
                    "72h": {
                        "source": "youtube_analytics",
                        "score": 80,
                        "scores": {
                            "reach": 70,
                            "retention": 90,
                            "engagement": 70,
                            "conversion": 65,
                            "total": 80,
                        },
                        "subscribersGained": 5,
                    }
                },
            },
            "b": {
                "youtube_video_id": "yt-b",
                "title": "B",
                "metadata_arm": "stadium",
                "publish_time_arm": "18:30",
                "thumbnail_arm": "best_frame",
                "publish_at_local": "2026-08-29T18:30:00+08:00",
                "context": {
                    "person": "generic",
                    "duration_bucket": "46-60",
                    "opening": "static",
                    "weekday_group": "weekend",
                },
                "exact": {"24h": {"views": 400, "likes": 10, "comments": 1}},
                "analytics": {
                    "72h": {
                        "source": "youtube_analytics",
                        "score": 40,
                        "scores": {
                            "reach": 40,
                            "retention": 35,
                            "engagement": 45,
                            "conversion": 40,
                            "total": 40,
                        },
                        "subscribersGained": 1,
                    }
                },
            },
        }
    }
    config = {
        "youtube_channel_name": "象兒應援團",
        "youtube_channel_id": "channel",
        "metadata_arms": [{"id": "direct"}, {"id": "stadium"}],
        "publish_time_arms": ["18:30", "20:30"],
        "thumbnail_arms": ["best_frame", "youtube_default"],
        "experiment_plan": [
            {
                "name": "metadata",
                "assignment_key": "metadata_arm",
                "mature_window": "72h",
                "min_mature_samples": 3,
            }
        ],
    }
    now = datetime(2026, 8, 31, 2, 0, tzinfo=timezone.utc)
    dashboard = build_dashboard(state, config, now=now)
    assert dashboard["active_experiment"]["name"] == "metadata"
    assert dashboard["leaderboards"]["metadata"][0]["arm"] == "direct"
    assert dashboard["content_correlations"]["opening"][0]["group"] == "active"
    assert dashboard["top_videos"][0]["youtube_video_id"] == "yt-a"
    markdown = render_markdown(dashboard)
    assert "YT_MEDIA Growth Dashboard" in markdown
    assert "direct" in markdown
