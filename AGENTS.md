# AGENTS.md

## Mission
Maintain a GitHub Actions-hosted Google Drive → YouTube publishing agent for `象兒應援團`.

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
- `.github/workflows/publish.yml`: production scheduler/runtime on standard `ubuntu-latest`.
- GitHub Actions repository secrets: OAuth client + Drive token + YouTube token.
- Google Drive root `.YT_MEDIA_STATE.json`: persistent idempotency/recovery state.
- GitHub Actions `concurrency`: prevents overlapping scheduled runs.
- Local Windows scripts: OAuth bootstrap, one-time migration, and emergency console only. Do not reintroduce Windows Task Scheduler as production scheduling.
- Google Cloud Run / Cloud Scheduler / Secret Manager / Cloud Storage are not part of production and must not be reintroduced without an explicit architecture decision.

## Code map
- `src/yt_media/core.py`: config, metadata, scheduling, local state, state-merge safety.
- `src/yt_media/google_api.py`: OAuth + Drive + YouTube API boundaries and Drive-backed state file.
- `src/yt_media/media.py`: ffprobe / FFmpeg stream cleanup.
- `src/yt_media/agent.py`: orchestration, local/Drive state selection, migration CLI.
- `scripts/setup_github_actions.ps1`: one-time Windows → GitHub Actions cutover.
- `.github/workflows/publish.yml`: hourly publisher.

## Development
Run:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . pytest
.\.venv\Scripts\python.exe -m pytest -q
```

Use PRs for changes. Preserve idempotency, strict channel verification, and credential isolation.
