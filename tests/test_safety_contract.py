from pathlib import Path


def test_youtube_id_is_persisted_before_thumbnail_or_reconcile():
    source = (Path(__file__).resolve().parents[1] / "src" / "yt_media" / "agent.py").read_text(encoding="utf-8")
    upload_id = source.index('video_id = response["id"]')
    durable_id = source.index('"youtube_video_id": video_id', upload_id)
    first_save = source.index("store.save(state)", durable_id)
    thumbnail = source.index("set_best_thumbnail", upload_id)
    reconcile = source.index("reconcile_uploaded", upload_id)
    assert upload_id < durable_id < first_save < thumbnail
    assert first_save < reconcile
