# AGENTS.md

## Mission
Maintain a cloud-hosted Google Drive → YouTube publishing agent for `象兒應援團`.

## Non-negotiable safety
- Never commit OAuth client secrets, refresh/access tokens, downloaded videos, or runtime state.
- Never upload if authenticated YouTube channel ID is not `UCzqapvxqSNMeNEM2ng91sow`.
- Never re-upload a Drive file that already has a persisted `youtube_video_id`.
- Persist `youtube_video_id` before moving the Drive original.
- Move a Drive original to `04_已上傳` only after YouTube processing succeeds.
- Do not delete source videos.
- `02_待剪輯`, `03_重複待刪除`, and `04_已上傳` remain excluded.

## Production architecture
- GitHub `main`: source of truth.
- GitHub Actions: keyless deployment via Google Workload Identity Federation.
- Cloud Run Job `yt-media-autopublisher`: production runtime.
- Cloud Scheduler `yt-media-hourly`: hourly trigger in `Asia/Taipei`.
- Secret Manager: OAuth client + Drive token + YouTube token.
- Cloud Storage: persistent `state.json` and distributed execution lock.
- Cloud Logging: production stdout/stderr logs.
- Local Windows scripts: bootstrap/migration/emergency console only; do not reintroduce Windows Task Scheduler as the production scheduler.

## Code map
- `src/yt_media/core.py`: config, metadata, scheduling, local/GCS state, cloud execution lock.
- `src/yt_media/google_api.py`: OAuth + Drive + YouTube API boundaries; supports read-only Secret Manager mounts.
- `src/yt_media/media.py`: ffprobe / FFmpeg stream cleanup.
- `src/yt_media/agent.py`: orchestration and CLI.
- `scripts/deploy_cloud.ps1`: one-time cloud migration and WIF bootstrap.
- `.github/workflows/deploy-cloud.yml`: continuous deployment from `main`.

## Development
Run:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . pytest
.\.venv\Scripts\python.exe -m pytest -q
```

Use PRs for changes. Preserve idempotency, strict channel verification, and credential isolation.
