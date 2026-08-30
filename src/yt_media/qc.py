from __future__ import annotations

import hashlib
import json
import math
import shutil
import statistics
import subprocess
from pathlib import Path
from typing import Any

from .core import media_traits


def _sample_times(duration: float, count: int = 5) -> list[float]:
    if duration <= 0:
        return []
    fractions = [0.08, 0.27, 0.50, 0.73, 0.92]
    return [max(0.0, min(duration - 0.05, duration * f)) for f in fractions[:count]]


def _gray_frame(path: Path, at_seconds: float) -> bytes:
    exe = shutil.which("ffmpeg")
    if not exe:
        return b""
    try:
        result = subprocess.run(
            [
                exe,
                "-v", "error",
                "-ss", f"{at_seconds:.3f}",
                "-i", str(path),
                "-frames:v", "1",
                "-vf", "scale=8:8,format=gray",
                "-f", "rawvideo",
                "-",
            ],
            check=True,
            capture_output=True,
        )
        return bytes(result.stdout[:64])
    except subprocess.CalledProcessError:
        return b""


def _average_hash(frame: bytes) -> str:
    if len(frame) < 64:
        return ""
    mean = sum(frame) / len(frame)
    bits = 0
    for value in frame[:64]:
        bits = (bits << 1) | (1 if value >= mean else 0)
    return f"{bits:016x}"


def _hamming_hex(a: str, b: str) -> int:
    if not a or not b:
        return 64
    try:
        return (int(a, 16) ^ int(b, 16)).bit_count()
    except ValueError:
        return 64


def media_fingerprint(path: Path, media_info: dict[str, Any]) -> dict[str, Any]:
    traits = media_traits(media_info)
    frames: list[bytes] = []
    hashes: list[str] = []
    for at in _sample_times(float(traits.get("duration") or 0)):
        frame = _gray_frame(path, at)
        if len(frame) >= 64:
            frames.append(frame)
            hashes.append(_average_hash(frame))

    brightness = [sum(frame) / len(frame) for frame in frames]
    contrast = [statistics.pstdev(frame) for frame in frames]
    digest = hashlib.sha256("|".join(hashes).encode("ascii")).hexdigest() if hashes else ""
    return {
        "version": 1,
        "duration": round(float(traits.get("duration") or 0), 3),
        "width": int(traits.get("width") or 0),
        "height": int(traits.get("height") or 0),
        "fps": round(float(traits.get("fps") or 0), 3),
        "hashes": hashes,
        "digest": digest,
        "mean_brightness": round(sum(brightness) / len(brightness), 2) if brightness else None,
        "mean_contrast": round(sum(contrast) / len(contrast), 2) if contrast else None,
    }


def fingerprint_distance(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_hashes = list(left.get("hashes") or [])
    right_hashes = list(right.get("hashes") or [])
    pairs = list(zip(left_hashes, right_hashes))
    if not pairs:
        return 64.0
    return sum(_hamming_hex(a, b) for a, b in pairs) / len(pairs)


def find_duplicate(
    fingerprint: dict[str, Any],
    files_state: dict[str, Any],
    current_file_id: str,
    duration_tolerance: float = 1.5,
    hamming_threshold: float = 2.0,
) -> dict[str, Any] | None:
    duration = float(fingerprint.get("duration") or 0)
    hashes = fingerprint.get("hashes") or []
    if not hashes:
        return None
    for file_id, entry in files_state.items():
        if file_id == current_file_id or not isinstance(entry, dict):
            continue
        other = entry.get("media_fingerprint")
        if not isinstance(other, dict) or not other.get("hashes"):
            continue
        if abs(duration - float(other.get("duration") or 0)) > duration_tolerance:
            continue
        distance = fingerprint_distance(fingerprint, other)
        if distance <= hamming_threshold:
            return {
                "drive_file_id": file_id,
                "youtube_video_id": entry.get("youtube_video_id"),
                "distance": round(distance, 3),
            }
    return None


def quality_report(
    path: Path,
    media_info: dict[str, Any],
    files_state: dict[str, Any],
    current_file_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    traits = media_traits(media_info)
    blockers: list[str] = []
    warnings: list[str] = []

    duration = float(traits.get("duration") or 0)
    width = int(traits.get("width") or 0)
    height = int(traits.get("height") or 0)
    if duration <= 0 or width <= 0 or height <= 0:
        blockers.append("unreadable_video_stream")
    if 0 < duration < float(config.get("qc_min_duration_seconds", 2.0)):
        blockers.append("too_short")
    if width and height and min(width, height) < int(config.get("qc_warn_short_edge", 720)):
        warnings.append("low_resolution")

    fingerprint = media_fingerprint(path, media_info)
    duplicate = find_duplicate(
        fingerprint,
        files_state,
        current_file_id,
        duration_tolerance=float(config.get("duplicate_duration_tolerance_seconds", 1.5)),
        hamming_threshold=float(config.get("duplicate_hamming_threshold", 2.0)),
    )
    if duplicate:
        blockers.append("probable_duplicate")

    hashes = fingerprint.get("hashes") or []
    if len(hashes) >= 3:
        distances = [_hamming_hex(a, b) for a, b in zip(hashes, hashes[1:])]
        if distances and sum(distances) / len(distances) <= float(config.get("qc_static_hamming_warning", 1.5)):
            warnings.append("very_low_visual_change")
    brightness = fingerprint.get("mean_brightness")
    contrast = fingerprint.get("mean_contrast")
    if brightness is not None and float(brightness) < float(config.get("qc_dark_brightness_warning", 8.0)):
        warnings.append("very_dark")
    if contrast is not None and float(contrast) < float(config.get("qc_low_contrast_warning", 4.0)):
        warnings.append("very_low_contrast")

    return {
        "ok": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "traits": traits,
        "fingerprint": fingerprint,
        "duplicate": duplicate,
        "file": str(path.name),
    }
