from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import load_config, make_metadata, merge_state_documents, read_state, runtime_dir, write_state
from .google_api import (
    authorize_drive,
    authorize_youtube,
    build_drive,
    build_youtube,
    discover_videos,
    download_file,
    ensure_folder,
    existing_publish_times,
    move_file,
    processing_status,
    read_drive_state,
    upload_video,
    write_drive_state,
)
from .media import probe, remux_primary_streams
from .qc import quality_report
from .strategy import arm_statistics, choose_arm, metadata_config_for_arm, schedule_slots_for_times
from .thumbnail import set_best_thumbnail

REPO_ROOT = Path(__file__).resolve().parents[2]


def _paths() -> dict[str, Path]:
    root = runtime_dir()
    return {
        "root": root,
        "state": root / "state.json",
        "log": root / "logs" / "agent.jsonl",
        "work": root / "work",
        "client": Path(os.environ.get("YT_MEDIA_CLIENT_SECRET_PATH", str(root / "client_secret.json"))),
        "drive_token": Path(os.environ.get("YT_MEDIA_DRIVE_TOKEN_PATH", str(root / "drive_token.json"))),
        "youtube_token": Path(os.environ.get("YT_MEDIA_YOUTUBE_TOKEN_PATH", str(root / "youtube_token.json"))),
    }


class StateStore:
    def __init__(self, drive, config: dict[str, Any], local_path: Path):
        self.drive = drive
        self.root_id = str(config["drive_root_folder_id"])
        self.local_path = local_path
        self.backend = os.environ.get("YT_MEDIA_STATE_BACKEND", "local").strip().lower()
        self.drive_file_id: str | None = None
        if self.backend not in {"local", "drive"}:
            raise RuntimeError("YT_MEDIA_STATE_BACKEND must be 'local' or 'drive'")

    def load(self) -> dict[str, Any]:
        if self.backend == "drive":
            state, self.drive_file_id = read_drive_state(self.drive, self.root_id)
            return state
        return read_state(self.local_path)

    def save(self, state: dict[str, Any]) -> None:
        if self.backend == "drive":
            self.drive_file_id = write_drive_state(
                self.drive,
                self.root_id,
                state,
                file_id=self.drive_file_id,
            )
            return
        write_state(self.local_path, state)

    @property
    def label(self) -> str:
        if self.backend == "drive":
            return "Google Drive root/.YT_MEDIA_STATE.json"
        return str(self.local_path)


def log_event(event: dict[str, Any]) -> None:
    path = _paths()["log"]
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"time": datetime.now(timezone.utc).isoformat(), **event}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps(record, ensure_ascii=False, indent=2), flush=True)


def doctor(config: dict[str, Any]) -> int:
    paths = _paths()
    result: dict[str, Any] = {
        "runtime": str(paths["root"]),
        "state_backend": os.environ.get("YT_MEDIA_STATE_BACKEND", "local"),
        "client_secret": paths["client"].exists(),
        "drive_token": paths["drive_token"].exists(),
        "youtube_token": paths["youtube_token"].exists(),
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
        "drive": None,
        "youtube": None,
    }
    try:
        drive, _ = build_drive(False)
        root = drive.files().get(fileId=config["drive_root_folder_id"], fields="id,name").execute()
        result["drive"] = {"ok": True, "root": root}
        store = StateStore(drive, config, paths["state"])
        state = store.load()
        result["state"] = {
            "ok": True,
            "backend": store.label,
            "entries": len(state.get("files", {})),
            "strategy": state.get("strategy", {}),
        }
    except Exception as exc:
        result["drive"] = {"ok": False, "error": str(exc)}
        result["state"] = {"ok": False, "error": str(exc)}
    try:
        _, _, channel = build_youtube(config, False)
        result["youtube"] = {"ok": True, "channel": channel}
    except Exception as exc:
        result["youtube"] = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["drive"]["ok"] and result["youtube"]["ok"] and result["state"]["ok"] else 1


def queue_report(config: dict[str, Any]) -> int:
    drive, _ = build_drive(False)
    store = StateStore(drive, config, _paths()["state"])
    state = store.load()
    videos = discover_videos(drive, config)
    rows = []
    for item in videos:
        entry = state.get("files", {}).get(item.file_id, {})
        rows.append({
            "id": item.file_id,
            "name": item.name,
            "priority": item.priority,
            "folder": item.parent_name,
            "state": entry.get("status", "new"),
            "youtube_video_id": entry.get("youtube_video_id"),
            "metadata_arm": entry.get("metadata_arm"),
            "publish_time_arm": entry.get("publish_time_arm"),
            "analytics": entry.get("analytics"),
        })
    print(json.dumps({"count": len(rows), "state_backend": store.label, "videos": rows}, ensure_ascii=False, indent=2))
    return 0


def seed_drive_state(config: dict[str, Any]) -> int:
    paths = _paths()
    if not paths["state"].exists():
        raise RuntimeError(
            f"Local state does not exist: {paths['state']}. "
            "Do not start the GitHub Actions cutover until the previous Windows state is available."
        )
    local_state = read_state(paths["state"])
    drive, _ = build_drive(False)
    root_id = str(config["drive_root_folder_id"])
    remote_state, remote_id = read_drive_state(drive, root_id)
    merged = merge_state_documents(local_state, remote_state)
    remote_id = write_drive_state(drive, root_id, merged, file_id=remote_id)
    print(json.dumps({
        "ok": True,
        "drive_state_file_id": remote_id,
        "local_entries": len(local_state.get("files", {})),
        "existing_drive_entries": len(remote_state.get("files", {})),
        "merged_entries": len(merged.get("files", {})),
    }, ensure_ascii=False, indent=2))
    return 0


def reconcile_uploaded(drive, youtube, item, entry, config, state, store: StateStore) -> bool:
    video_id = entry.get("youtube_video_id")
    if not video_id or entry.get("moved"):
        return False
    status = processing_status(youtube, video_id)
    entry["youtube_processing_status"] = status
    if status in {"failed", "rejected", "missing"}:
        entry["status"] = "error"
        entry["last_error"] = f"YouTube processing status: {status}"
        return True
    if status not in {"succeeded", "processed", "completed"}:
        entry["status"] = "processing"
        return True
    done_id = ensure_folder(drive, item.done_parent_id, config["done_folder_name"])
    move_file(drive, item.file_id, item.parent_id, done_id)
    entry["moved"] = True
    entry["status"] = "done"
    entry["moved_at"] = datetime.now(timezone.utc).isoformat()
    log_event({"event": "move_done", "drive_file_id": item.file_id, "youtube_video_id": video_id})
    return True


def _metadata_arm_ids(config: dict[str, Any]) -> list[str]:
    return [
        str(item["id"])
        for item in config.get("metadata_arms", [])
        if isinstance(item, dict) and item.get("id")
    ]


def _identity_aware_item(item, config: dict[str, Any]):
    """Allow an explicit person folder name to provide identity metadata.

    This is not face recognition. A person is used only when their configured
    name literally appears in the immediate Drive folder name or filename.
    """
    source = re.sub(r"[\s_\-]", "", f"{item.parent_name}{item.name}").lower()
    filename = re.sub(r"[\s_\-]", "", item.name).lower()
    for person in config.get("known_people", []):
        token = re.sub(r"[\s_\-]", "", str(person)).lower()
        if token and token in source and token not in filename:
            return replace(item, name=f"{person}_{item.name}")
    return item


def run_once(config: dict[str, Any]) -> int:
    paths = _paths()
    paths["work"].mkdir(parents=True, exist_ok=True)
    drive, _ = build_drive(False)
    youtube, _, channel = build_youtube(config, False)
    store = StateStore(drive, config, paths["state"])
    state = store.load()
    files_state = state.setdefault("files", {})
    videos = discover_videos(drive, config)

    for item in videos:
        entry = files_state.get(item.file_id)
        if entry and entry.get("youtube_video_id") and not entry.get("moved"):
            try:
                reconcile_uploaded(drive, youtube, item, entry, config, state, store)
            finally:
                store.save(state)

    blocked_statuses = {"qc_rejected"}
    pending = [
        item for item in videos
        if not files_state.get(item.file_id, {}).get("youtube_video_id")
        and not files_state.get(item.file_id, {}).get("moved")
        and files_state.get(item.file_id, {}).get("status") not in blocked_statuses
    ][: int(config.get("max_uploads_per_run", 8))]

    if not pending:
        print("沒有新的待上傳影片。")
        return 0

    exploration = float(config.get("strategy_exploration", 0.35))
    metadata_arms = _metadata_arm_ids(config) or ["default"]
    metadata_stats = arm_statistics(state, "metadata_arm", metadata_arms)
    selected_metadata_arms = [
        choose_arm(item.file_id, metadata_arms, metadata_stats, salt="metadata", exploration=exploration)
        for item in pending
    ]

    publish_arms = [str(x) for x in config.get("publish_time_arms", []) if str(x)]
    if not publish_arms or not bool(config.get("publish_time_experiment_enabled", False)):
        publish_arms = [str(config.get("publish_time", "18:30"))]
    publish_stats = arm_statistics(state, "publish_time_arm", publish_arms)
    selected_publish_arms = [
        choose_arm(item.file_id, publish_arms, publish_stats, salt="publish-time", exploration=exploration)
        for item in pending
    ]

    occupied = existing_publish_times(youtube)
    slots = schedule_slots_for_times(config, selected_publish_arms, occupied)
    print(f"已驗證頻道：{channel['title']} ({channel['id']})")
    print(f"State backend：{store.label}")
    print(f"本次準備處理 {len(pending)} 支影片。")
    print(json.dumps({
        "metadata_arm_stats": metadata_stats,
        "publish_time_stats": publish_stats,
        "planned_publish_times": selected_publish_arms,
    }, ensure_ascii=False, indent=2))

    for item, slot, metadata_arm, publish_time_arm in zip(
        pending,
        slots,
        selected_metadata_arms,
        selected_publish_arms,
    ):
        entry = files_state.setdefault(item.file_id, {})
        work_dir = paths["work"] / item.file_id
        work_dir.mkdir(parents=True, exist_ok=True)
        original = work_dir / item.name
        cleaned = work_dir / f"{Path(item.name).stem}_clean.mp4"
        try:
            log_event({"event": "download_start", "file": item.name, "drive_file_id": item.file_id})
            download_file(drive, item.file_id, original, lambda p: print(f"下載 {item.name}: {p * 100:.1f}%", flush=True))
            upload_path = remux_primary_streams(original, cleaned, bool(config.get("remux_with_ffmpeg", True)))
            media_info = probe(upload_path)

            if bool(config.get("qc_enabled", True)):
                qc = quality_report(upload_path, media_info, files_state, item.file_id, config)
                entry["media_fingerprint"] = qc["fingerprint"]
                entry["qc_warnings"] = qc["warnings"]
                entry["qc_checked_at"] = datetime.now(timezone.utc).isoformat()
                if not qc["ok"]:
                    entry["status"] = "qc_rejected"
                    entry["qc_blockers"] = qc["blockers"]
                    entry["duplicate_of"] = qc.get("duplicate")
                    store.save(state)
                    log_event({
                        "event": "qc_rejected",
                        "file": item.name,
                        "drive_file_id": item.file_id,
                        "blockers": qc["blockers"],
                        "warnings": qc["warnings"],
                        "duplicate": qc.get("duplicate"),
                    })
                    shutil.rmtree(work_dir, ignore_errors=True)
                    continue
                if qc["warnings"]:
                    log_event({
                        "event": "qc_warning",
                        "file": item.name,
                        "drive_file_id": item.file_id,
                        "warnings": qc["warnings"],
                    })

            arm_config = metadata_config_for_arm(config, metadata_arm)
            metadata_item = _identity_aware_item(item, config)
            metadata = make_metadata(metadata_item, media_info, arm_config)
            log_event({
                "event": "upload_start",
                "file": item.name,
                "title": metadata["title"],
                "metadata_arm": metadata_arm,
                "publish_time_arm": publish_time_arm,
                "publish_at": slot.isoformat(),
            })
            response = upload_video(
                youtube,
                upload_path,
                metadata,
                slot,
                config,
                lambda p: print(f"上傳 {item.name}: {p * 100:.1f}%", flush=True),
            )
            video_id = response["id"]

            thumbnail_result = None
            if bool(config.get("thumbnail_best_effort", True)):
                thumbnail_result = set_best_thumbnail(youtube, video_id, upload_path, media_info, work_dir)
                log_event({
                    "event": "thumbnail_result",
                    "file": item.name,
                    "youtube_video_id": video_id,
                    "ok": bool(thumbnail_result.get("ok")),
                    "reason": thumbnail_result.get("reason"),
                    "selection": thumbnail_result.get("selection"),
                })

            entry.update({
                "status": "uploaded",
                "youtube_video_id": video_id,
                "youtube_url": f"https://youtu.be/{video_id}",
                "title": metadata["title"],
                "metadata_version": metadata.get("metadata_version"),
                "metadata_arm": metadata_arm,
                "publish_time_arm": publish_time_arm,
                "publish_at_local": slot.isoformat(),
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "thumbnail": thumbnail_result,
                "moved": False,
            })
            store.save(state)
            log_event({
                "event": "upload_success",
                "file": item.name,
                "youtube_video_id": video_id,
                "youtube_url": entry["youtube_url"],
                "metadata_arm": metadata_arm,
                "publish_time_arm": publish_time_arm,
                "publish_at": slot.isoformat(),
            })
            reconcile_uploaded(drive, youtube, item, entry, config, state, store)
            store.save(state)
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception as exc:
            entry["status"] = "error"
            entry["last_error"] = str(exc)
            entry["last_error_at"] = datetime.now(timezone.utc).isoformat()
            store.save(state)
            log_event({"event": "error", "file": item.name, "error_type": type(exc).__name__, "error": str(exc)})
            if config.get("stop_on_first_error", True):
                raise
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="YT_MEDIA Drive → YouTube Agent")
    parser.add_argument("command", choices=["authorize", "doctor", "queue", "run", "seed-drive-state"])
    args = parser.parse_args()
    config = load_config(REPO_ROOT)

    if args.command == "authorize":
        paths = _paths()
        paths["drive_token"].unlink(missing_ok=True)
        paths["youtube_token"].unlink(missing_ok=True)
        print("[1/2] Google Drive 授權")
        print(json.dumps(authorize_drive(), ensure_ascii=False, indent=2))
        print("[2/2] YouTube 授權與目標頻道驗證")
        print(json.dumps(authorize_youtube(config), ensure_ascii=False, indent=2))
        return doctor(config)
    if args.command == "doctor":
        return doctor(config)
    if args.command == "queue":
        return queue_report(config)
    if args.command == "seed-drive-state":
        return seed_drive_state(config)
    if args.command == "run":
        return run_once(config)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
