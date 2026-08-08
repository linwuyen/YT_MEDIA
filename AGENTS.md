# AGENTS.md

## Mission
Maintain a Windows-hosted Google Drive → YouTube publishing agent for `象兒應援團`.

## Non-negotiable safety
- Never commit OAuth client secrets, refresh/access tokens, downloaded videos, or runtime state.
- Never upload if authenticated YouTube channel ID is not `UCzqapvxqSNMeNEM2ng91sow`.
- Never re-upload a Drive file that already has a persisted `youtube_video_id`.
- Move a Drive original to `04_已上傳` only after the YouTube upload exists and processing has succeeded.
- Do not delete source videos.
- Keep runtime data under `%LOCALAPPDATA%\YT_MEDIA`.

## Architecture
- `src/yt_media/core.py`: pure config/metadata/scheduling/state logic.
- `src/yt_media/google_api.py`: Google OAuth + Drive + YouTube API boundaries.
- `src/yt_media/media.py`: ffprobe / FFmpeg stream cleanup.
- `src/yt_media/agent.py`: orchestration and CLI.
- `scripts/`: Windows bootstrap and Task Scheduler integration.

## Development
Run:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . pytest
.\.venv\Scripts\python.exe -m pytest -q
```

Use PRs for changes after initial bootstrap. Prefer small, reversible changes and preserve the state-machine guarantees above.
