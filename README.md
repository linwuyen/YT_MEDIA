# YT_MEDIA — Google Drive → YouTube 全自動發布

日常目標只有一個：**把影片丟進 Google Drive，電腦可以關機。**

正式版架構：

```text
Google Drive「建發用」
        ↓
GitHub Actions（每小時）
        ↓
Drive 掃描 / FFmpeg / metadata / YouTube API
        ↓
YouTube「象兒應援團」排程發布
        ↓
YouTube processing 成功後移到 04_已上傳
```

不需要 Google Cloud Billing。Cloud Run / Cloud Scheduler / Secret Manager / Cloud Storage 已退出正式架構。

## 已綁定環境

- Google Drive 根目錄：`建發用`
- Drive Folder ID：`1vg-sHZfam52sAZWqMu6uIHhUUqSZEC8x`
- YouTube 頻道：`象兒應援團`
- Channel ID：`UCzqapvxqSNMeNEM2ng91sow`
- 預設發布時間：每天 `18:30`（Asia/Taipei）
- 每輪最多：8 支
- GitHub workflow：`.github/workflows/publish.yml`
- Persistent state：Google Drive 根目錄 `.YT_MEDIA_STATE.json`

## 日常使用

只做：

```text
手機/相機影片 → Google Drive「建發用」底下任一日期資料夾
```

Agent 會遞迴掃描：

```text
01_優先上傳      → 優先處理
一般日期資料夾    → 處理
02_待剪輯        → 忽略
03_重複待刪除    → 忽略
04_已上傳        → 忽略
```

日期資料夾沒有 `01_優先上傳` 時，直接放 MP4/MOV/MKV 也會處理。

## 一次性：Windows → GitHub Actions

已完成本機 OAuth 的 Windows PC 執行：

```powershell
cd C:\YT_MEDIA
git pull --ff-only origin main
.\SETUP_GITHUB_ACTIONS.cmd
```

腳本會：

1. 確認本機 `client_secret.json` / Drive token / YouTube token / `state.json` 都存在。
2. 暫停 Windows `YT_MEDIA_AutoPublisher`，並等目前 Agent 完全停止。
3. 將本機 `state.json` 安全合併到 Drive 根目錄 `.YT_MEDIA_STATE.json`；同一 Drive file 若出現不同 YouTube ID 會直接中止，不猜測、不覆蓋。
4. 將三份 OAuth JSON 透過 GitHub CLI 寫入 GitHub Actions repository secrets；不 commit 到 repo。
5. 觸發第一次 `publish.yml` 並等待驗收結果。
6. 只有 Actions 驗收成功才刪除 Windows scheduled task；失敗時若原本 Windows task 是啟用狀態會自動恢復。

成功後你的 PC 不需要保持開機。

## GitHub Actions 正式執行

`publish.yml` 每小時第 17 分執行一次，刻意避開整點高峰；也可以手動 `workflow_dispatch`。

```text
GitHub-hosted ubuntu-latest
        ↓
GitHub Secrets 還原 OAuth 檔到 runner 暫存
        ↓
YT_MEDIA_STATE_BACKEND=drive
        ↓
驗證 Drive + YouTube 頻道
        ↓
讀 .YT_MEDIA_STATE.json
        ↓
掃描 Drive
        ↓
最多處理 8 支
        ↓
單支下載 → ffprobe/FFmpeg → YouTube upload
        ↓
立刻把 youtube_video_id 寫回 Drive state
        ↓
YouTube processing 成功才移到 04_已上傳
```

Workflow 使用 `concurrency`，同一時間只允許一個 publisher run，避免兩輪同時處理同一批影片。

## 防重複 / 當機恢復

最重要的邊界：

```text
YouTube API 回傳 video_id
        ↓
先寫 .YT_MEDIA_STATE.json
        ↓
才做後續 processing / Drive move
```

因此 runner 在「upload 成功」後即使中斷，下一輪只會 reconcile 該 YouTube video，不會重新上傳同一支 Drive 檔案。

## Secrets

GitHub Actions 使用三個 repository secrets：

```text
YT_MEDIA_CLIENT_SECRET_JSON
YT_MEDIA_DRIVE_TOKEN_JSON
YT_MEDIA_YOUTUBE_TOKEN_JSON
```

由 `SETUP_GITHUB_ACTIONS.cmd` 從 `%LOCALAPPDATA%\YT_MEDIA` 直接寫入 GitHub Secrets。不要把 OAuth JSON 貼到 Issue、PR、README 或 commit。

## 排程長期存活

GitHub 對 public repo 若 60 天沒有 repository activity，可能自動停用 scheduled workflows。`keepalive.yml` 每月建立一個 `[skip ci]` 空 commit，避免 publisher 因 inactivity 被停掉。

## 人物名稱

人物只從檔名判斷，不做臉部辨識。

例如：

```text
李珠垠_001.mp4
```

可自動帶名字；若是：

```text
video_20260813_183252.mp4
```

就使用通用應援標題，不猜真人身份。

## 本機工具

本機保留作 OAuth/bootstrap/emergency console：

```powershell
.\scripts\doctor.ps1
.\scripts\queue.ps1
.\RUN_NOW.cmd
```

但 Actions cutover 成功後，不再依賴 Windows Task Scheduler。

## 不可破壞的安全條件

- OAuth YouTube Channel ID 不等於 `UCzqapvxqSNMeNEM2ng91sow` 就拒絕執行。
- 已持久化 `youtube_video_id` 就不重新上傳。
- YouTube processing 成功後才移動 Drive 原片。
- 不刪除原始影片。
- `02_待剪輯`、`03_重複待刪除`、`04_已上傳` 永遠不掃描。
