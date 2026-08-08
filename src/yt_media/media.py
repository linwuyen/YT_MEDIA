from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


def probe(path: Path) -> dict[str, Any]:
    exe = shutil.which("ffprobe")
    if not exe:
        return {}
    try:
        result = subprocess.run(
            [
                exe, "-v", "error",
                "-show_entries", "format=duration:stream=index,codec_type,width,height,r_frame_rate",
                "-of", "json", str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return {}


def remux_primary_streams(source: Path, target: Path, enabled: bool = True) -> Path:
    exe = shutil.which("ffmpeg")
    if not enabled or not exe:
        return source
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                exe, "-y", "-i", str(source),
                "-map", "0:v:0", "-map", "0:a:0?",
                "-c", "copy", "-movflags", "+faststart",
                str(target),
            ],
            check=True,
        )
        return target
    except subprocess.CalledProcessError:
        target.unlink(missing_ok=True)
        return source
