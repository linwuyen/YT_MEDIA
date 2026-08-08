# YT_MEDIA — Google Drive → YouTube 全自動發布

目標只有一個：**日常使用時，你只負責把影片丟進 Google Drive。電腦可以關機。**

正式版架構已改成：

```text
Google Drive「建發用」
        ↓
Google Cloud Scheduler（每小時）
        ↓
Cloud Run Job
        ↓
Drive 掃描 / FFmpeg / metadata / YouTube API
        ↓
YouTube「象兒應援團」排程發布
        ↓
成功後移到 04_已上傳
```

程式碼放在 GitHub；OAuth 憑證放 Google Secret Manager；防重複狀態放 Cloud Storage。Windows Task Scheduler 不再是正式執行環境。

## 已綁定環境

- Google Drive 根目錄：`建發用`
- Drive Folder ID：`1vg-sHZfam52sAZWqMu6uIHhUUqSZEC8x`
- YouTube 頻道：`象兒應援團`
- Channel ID：`UCzqapvxqSNMeNEM2ng91sow`
- 預設發布時間：每天 `18:30`（Asia/Taipei）
- 每輪最多：8 支
- Cloud Run Job：`yt-media-autopublisher`
- Cloud Scheduler：`yt-media-hourly`

## 日常使用

只做這件事：

```text
手機/相機影片 → Google Drive「建發用」底下任一日期資料夾
```

Agent 會遞迴掃描：

```text
01_優先上傳      → 優先處理
一般日期資料夾    → 也會處理
02_待剪輯        → 忽略
03_重複待刪除    → 忽略
04_已上傳        → 忽略
```

如果日期資料夾沒有 `01_優先上傳`，直接放 MP4/MOV/MKV 也可以。

## 從 Windows 版遷移到雲端：只做一次

你已經完成 Drive + YouTube OAuth 後，在本機 repo 執行：

```powershell
cd C:\YT_MEDIA
git pull --ff-only origin main
.\DEPLOY_CLOUD.cmd
```

`DEPLOY_CLOUD.cmd` 會自動完成：

1. 檢查目前已成功取得的 `client_secret.json` / Drive token / YouTube token。
2. 安裝或尋找 Google Cloud CLI。
3. 讓你確認要使用的 Google Cloud Project。
4. 啟用 Cloud Run、Cloud Scheduler、Secret Manager、Cloud Storage、Cloud Build、Artifact Registry、IAM/WIF 等 API。
5. 建立 Cloud Run runtime / Scheduler / GitHub deployer service accounts。
6. 建立 Cloud Storage state bucket。
7. 把 OAuth JSON/token 放進 Secret Manager；**不放 GitHub**。
8. 從目前 repo source build 並部署 Cloud Run Job。
9. 建立每小時 Cloud Scheduler trigger。
10. 建立 GitHub Actions → Google Cloud 的 Workload Identity Federation（無長效 service-account key）。
11. 將 Google Cloud 相關的非敏感參數寫入 GitHub repository variables。
12. 執行一次 Cloud Run Job 做驗收。
13. **只有驗收成功後**才移除 Windows `YT_MEDIA_AutoPublisher` 排程。

Google Cloud 專案必須可使用 Cloud Run 等付費型資源；若專案尚未連結 Billing，Google Cloud 會在部署階段要求先啟用 Billing。

## 正式執行流程

```text
Cloud Scheduler
每小時整點 / Asia/Taipei
        ↓
Cloud Run Job: yt-media-autopublisher
        ↓
Secret Manager
client_secret + Drive token + YouTube token
        ↓
嚴格驗證 Channel ID
UCzqapvxqSNMeNEM2ng91sow
        ↓
掃描 Google Drive
        ↓
01_優先上傳優先
        ↓
下載單支影片至 Cloud Run 暫存
        ↓
ffprobe + FFmpeg remux
        ↓
產生標題 / 說明 / hashtag
        ↓
檢查 YouTube 已有 publishAt，避免撞時間
        ↓
安排下一個 18:30 空位
        ↓
上傳 YouTube
        ↓
立刻把 youtube_video_id 寫入 Cloud Storage state.json
        ↓
YouTube processing 成功？
   ├─ 否：下一輪繼續檢查，不重傳
   └─ 是：Drive 原片移到 04_已上傳
```

## 防重複 / 當機恢復

正式版的 state 不再依賴本機檔案，而是：

```text
gs://<PROJECT_ID>-yt-media-state/state.json
```

Cloud Run 在 YouTube API 回傳 `video_id` 後，會先持久化 state，再進行後續處理。即使 container 在「上傳成功 → 移動 Drive」之間結束，下一輪只會 reconcile 該 YouTube 影片，不會重新 upload。

另外會使用同一 bucket 的 lock object 避免兩個 Cloud Run executions 同時處理同一批檔案；異常留下的 lock 會在 6 小時後視為 stale 並自動恢復。

## OAuth / Secret 安全

正式 Cloud Run 使用 Secret Manager mount：

```text
/secrets/client_secret.json
/secrets/drive_token.json
/secrets/youtube_token.json
```

Secret volume 是唯讀；refresh token 用來取得新的 access token，新的 access token 只需存在當次 execution 記憶體/暫存中。

永遠不要把下列內容 commit：

```text
client_secret*.json
*token*.json
gha-creds-*.json
影片
state.json
```

## GitHub 自動部署

第一次 `DEPLOY_CLOUD.cmd` 會建立 Workload Identity Federation，並設定 repository variables。之後只要 `main` 的 runtime 相關程式變更：

```text
GitHub main
   ↓
.github/workflows/deploy-cloud.yml
   ↓ OIDC / WIF（無 service account key）
Google Cloud
   ↓
Cloud Run Job 更新
```

因此未來修改 `src/`、`config/`、Dockerfile 或部署 workflow，不需要你的 Windows 電腦保持在線。

## 標題與人物名稱

若檔名包含已知人物名稱，例如：

```text
李珠垠_001.mp4
李雅英_002.mp4
```

標題會自動帶名字。

若只有：

```text
video_20260718_180654.mp4
```

Agent 不會猜真人身分，而是使用通用應援標題。人物清單與標題模板在 `config/default.json`。

## 本機工具還保留什麼

Windows repo 保留作為 bootstrap / migration / emergency console：

```powershell
.\scripts\doctor.ps1
.\scripts\queue.ps1
.\RUN_NOW.cmd
```

但雲端遷移成功後，日常發布不依賴這些指令，也不依賴 Windows Task Scheduler。

## CI

PR / main 仍會執行 Python compile + pytest。`main` 的 runtime 變更另外會觸發 Cloud Run deployment workflow。

## 不可破壞的安全條件

- OAuth YouTube Channel ID 不等於 `UCzqapvxqSNMeNEM2ng91sow` 就拒絕執行。
- 已有持久化 `youtube_video_id` 就不重新上傳。
- YouTube processing 成功後才移動 Drive 原片。
- 不刪除原始影片。
- `02_待剪輯`、`03_重複待刪除`、`04_已上傳` 永遠不掃描。
