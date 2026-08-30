from __future__ import annotations

import hashlib
import re
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
    motion = [_hamming_hex(a, b) for a, b in zip(hashes, hashes[1:])]
    return {
        "version": 2,
        "duration": round(float(traits.get("duration") or 0), 3),
        "width": int(traits.get("width") or 0),
        "height": int(traits.get("height") or 0),
        "fps": round(float(traits.get("fps") or 0), 3),
        "hashes": hashes,
        "digest": digest,
        "mean_brightness": round(sum(brightness) / len(brightness), 2) if brightness else None,
        "mean_contrast": round(sum(contrast) / len(contrast), 2) if contrast else None,
        "sampled_visual_change_index": round(sum(motion) / len(motion), 3) if motion else None,
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


def opening_second_report(path: Path, media_info: dict[str, Any]) -> dict[str, Any]:
    """Measure the first second without mutating the video."""
    duration = float(media_traits(media_info).get("duration") or 0)
    if duration <= 0:
        return {"available": False}
    times = [0.05, min(0.35, duration - 0.02), min(0.90, duration - 0.02)]
    frames: list[bytes] = []
    hashes: list[str] = []
    for at in times:
        if at < 0:
            continue
        frame = _gray_frame(path, at)
        if len(frame) >= 64:
            frames.append(frame)
            hashes.append(_average_hash(frame))
    if not frames:
        return {"available": False}

    brightness = [sum(frame) / len(frame) for frame in frames]
    contrast = [statistics.pstdev(frame) for frame in frames]
    motion = [_hamming_hex(a, b) for a, b in zip(hashes, hashes[1:])]
    mean_motion = sum(motion) / len(motion) if motion else 0.0
    mean_brightness = sum(brightness) / len(brightness)
    mean_contrast = sum(contrast) / len(contrast)
    likely_dead_air = bool(mean_brightness < 10 or (mean_motion <= 1.0 and mean_contrast < 8.0))
    return {
        "available": True,
        "mean_brightness": round(mean_brightness, 2),
        "mean_contrast": round(mean_contrast, 2),
        "mean_motion_hamming": round(mean_motion, 3),
        "likely_dead_air": likely_dead_air,
    }


def _ffmpeg_text(args: list[str]) -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        return ""
    try:
        result = subprocess.run(
            [exe, "-hide_banner", "-nostats", *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return ""
    return (result.stdout or "") + "\n" + (result.stderr or "")


def _audio_features(path: Path, media_info: dict[str, Any], analysis_seconds: float) -> dict[str, Any]:
    has_audio = any(stream.get("codec_type") == "audio" for stream in media_info.get("streams", []))
    if not has_audio:
        return {"available": True, "has_audio": False}
    text = _ffmpeg_text([
        "-t", f"{analysis_seconds:.3f}",
        "-i", str(path),
        "-vn",
        "-af", "silencedetect=noise=-35dB:d=0.35,volumedetect",
        "-f", "null",
        "-",
    ])
    mean_match = re.search(r"mean_volume:\s*(-?[0-9.]+)\s*dB", text)
    max_match = re.search(r"max_volume:\s*(-?[0-9.]+)\s*dB", text)
    silence_durations = [float(value) for value in re.findall(r"silence_duration:\s*([0-9.]+)", text)]
    silence_seconds = min(analysis_seconds, sum(silence_durations))
    return {
        "available": bool(text.strip()),
        "has_audio": True,
        "mean_volume_db": round(float(mean_match.group(1)), 2) if mean_match else None,
        "max_volume_db": round(float(max_match.group(1)), 2) if max_match else None,
        "silence_seconds": round(silence_seconds, 3),
        "silence_ratio": round(silence_seconds / analysis_seconds, 4) if analysis_seconds > 0 else None,
    }


def _scene_features(path: Path, analysis_seconds: float) -> dict[str, Any]:
    text = _ffmpeg_text([
        "-t", f"{analysis_seconds:.3f}",
        "-i", str(path),
        "-an",
        "-vf", "fps=2,scale=320:-2,select='gt(scene,0.32)',showinfo",
        "-f", "null",
        "-",
    ])
    count = len(re.findall(r"Parsed_showinfo[^\n]*pts_time:", text))
    density = count * 60.0 / analysis_seconds if analysis_seconds > 0 else 0.0
    return {
        "available": bool(text.strip()),
        "scene_changes": count,
        "scene_changes_per_minute": round(density, 3),
    }


def content_feature_report(
    path: Path,
    media_info: dict[str, Any],
    fingerprint: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Extract bounded, non-semantic content signals for later correlation.

    No face/person recognition is performed. Expensive sequential analysis is
    capped so a large file cannot consume the whole Actions run.
    """
    duration = float(media_traits(media_info).get("duration") or 0)
    cap = max(5.0, float(config.get("content_analysis_max_seconds", 45.0)))
    analysis_seconds = min(duration, cap) if duration > 0 else 0.0
    if analysis_seconds <= 0:
        return {"available": False}
    return {
        "available": True,
        "analysis_seconds": round(analysis_seconds, 3),
        "sampled_visual_change_index": fingerprint.get("sampled_visual_change_index"),
        "audio": _audio_features(path, media_info, analysis_seconds),
        "scene": _scene_features(path, analysis_seconds),
    }


def best_thumbnail_frame(path: Path, media_info: dict[str, Any], target: Path) -> dict[str, Any] | None:
    """Pick a visually healthy frame without trying to identify a person."""
    exe = shutil.which("ffmpeg")
    if not exe:
        return None
    traits = media_traits(media_info)
    duration = float(traits.get("duration") or 0)
    if duration <= 0:
        return None

    fractions = [0.03, 0.12, 0.28, 0.45, 0.62, 0.78, 0.92]
    candidates: list[tuple[float, float, float, float]] = []
    for fraction in fractions:
        at = max(0.0, min(duration - 0.05, duration * fraction))
        frame = _gray_frame(path, at)
        if len(frame) < 64:
            continue
        brightness = sum(frame) / len(frame)
        contrast = statistics.pstdev(frame)
        exposure_penalty = abs(brightness - 118.0) * 0.08
        score = contrast - exposure_penalty
        if brightness < 10 or brightness > 245:
            score -= 25
        candidates.append((score, at, brightness, contrast))
    if not candidates:
        return None

    score, at, brightness, contrast = max(candidates, key=lambda item: item[0])
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                exe,
                "-y",
                "-v", "error",
                "-ss", f"{at:.3f}",
                "-i", str(path),
                "-frames:v", "1",
                "-vf", "scale=720:-2",
                "-q:v", "3",
                str(target),
            ],
            check=True,
        )
    except subprocess.CalledProcessError:
        target.unlink(missing_ok=True)
        return None
    if not target.exists() or target.stat().st_size <= 0:
        return None
    return {
        "path": str(target),
        "at_seconds": round(at, 3),
        "visual_score": round(score, 3),
        "brightness": round(brightness, 2),
        "contrast": round(contrast, 2),
    }


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

    opening = opening_second_report(path, media_info)
    if opening.get("likely_dead_air"):
        warnings.append("first_second_likely_dead_air")

    content_features = content_feature_report(path, media_info, fingerprint, config)
    audio = content_features.get("audio", {}) if isinstance(content_features, dict) else {}
    mean_volume = audio.get("mean_volume_db") if isinstance(audio, dict) else None
    max_volume = audio.get("max_volume_db") if isinstance(audio, dict) else None
    silence_ratio = audio.get("silence_ratio") if isinstance(audio, dict) else None
    if mean_volume is not None and float(mean_volume) < -35.0:
        warnings.append("very_quiet_audio")
    if max_volume is not None and float(max_volume) >= -0.1:
        warnings.append("possible_audio_clipping")
    if silence_ratio is not None and float(silence_ratio) >= 0.80:
        warnings.append("mostly_silent_audio")

    return {
        "ok": not blockers,
        "blockers": blockers,
        "warnings": list(dict.fromkeys(warnings)),
        "traits": traits,
        "fingerprint": fingerprint,
        "opening_second": opening,
        "content_features": content_features,
        "duplicate": duplicate,
        "file": str(path.name),
    }
