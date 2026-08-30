# YT_MEDIA — Google Drive → YouTube 全自動發布

日常目標只有一個：**把影片丟進 Google Drive，電腦可以關機。**

正式版架構：

```text
Google Drive「建發用」
        ↓
GitHub Actions（每小時）
        ↓
Analytics feedback / metadata strategy / media QC
        ↓
Drive 掃描 / FFmpeg / YouTube API
        ↓
YouTube「象兒應援團」排程發布
        ↓
24h / 72h / 7d 成效回寫
        ↺
下一批自動偏向表現較好的 metadata / 發布時段
```

不需要 Google Cloud Billing。Cloud Run / Cloud Scheduler / Secret Manager / Cloud Storage 已退出正式架構。

## 已綁定環境

- Google Drive 根目錄：`建發用`
- Drive Folder ID：`1vg-sHZfam52sAZWqMu6uIHhUUqSZEC8x`
- YouTube 頻道：`象兒應援團`
- Channel ID：`UCzqapvxqSNMeNEM2ng91sow`
- 發布時段實驗：`17:30 / 18:30 / 19:30 / 20:30 / 21:30`（Asia/Taipei）
- 每個日期最多安排一支，避免同日互相吃流量
- 每輪最多處理：8 支
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

## 一次性 GitHub Actions / 管理 OAuth 設定

已完成本機 OAuth 的 Windows PC 執行：

```powershell
cd C:\YT_MEDIA
git pull --ff-only origin main
.\SETUP_GITHUB_ACTIONS.cmd
```

腳本會：

1. 確認本機 Drive / YouTube OAuth 與 durable state。
2. 建立獨立的 YouTube management OAuth，用於修改尚未公開影片 metadata 與讀取 Analytics。
3. 若本機已有已登入的 `gcloud`，best-effort 啟用免費的 YouTube Analytics API；失敗時 publisher 不會停，會先用 YouTube Data API statistics fallback。
4. 安全合併 state 到 Drive 根目錄 `.YT_MEDIA_STATE.json`。
5. 將 OAuth JSON 直接寫入 GitHub Actions repository secrets；不 commit 到 repo。
6. 觸發 `publish.yml` 驗收。

成功後 PC 不需要保持開機。

## GitHub Actions 正式執行

`publish.yml` 每小時第 17 分執行一次，並用 `concurrency` 保證同一時間只有一個 publisher。

```text
Verify Drive + target YouTube channel
        ↓
Collect Analytics / update strategy
        ↓
Refresh unpublished metadata
        ↓
Scan Drive
        ↓
Media QC + duplicate fingerprint
        ↓
Select metadata arm + publish-time arm
        ↓
Upload / best-effort thumbnail
        ↓
Persist youtube_video_id immediately
        ↓
YouTube processing success
        ↓
Move source to 04_已上傳
```

## 自動學習策略

這不是宣稱知道 YouTube 私有推薦公式，而是用**自己頻道的觀眾結果**做 channel-local optimization。

每支影片在發布後會盡量取得：

```text
24h
72h
7d
```

評分主要使用：

- averageViewPercentage / averageViewDuration
- engagedViews / views
- likes / comments / shares
- subscribersGained

如果 YouTube Analytics API 尚未啟用，會退化使用 Data API 的 views / likes / comments，等 Analytics API 可用後自動恢復完整訊號。

### Metadata multi-armed bandit

目前有 6 組 metadata strategy arm：

```text
direct
stadium
live
quality
moment
energy
```

每支影片仍保留 deterministic 文案差異，但 arm 會控制搜尋詞、hook 與 hashtag family。系統會給歷史表現好的 arm 更多流量，同時保留 exploration，避免永遠卡在舊策略。

### 發布時段實驗

未來新影片會在以下時段中自動分配：

```text
17:30
18:30
19:30
20:30
21:30
```

已排程日期整天視為 occupied，所以不會因時段實驗而在同一天塞兩支。

## Metadata 原則

- 標題重要搜尋詞放前面。
- 不在標題硬塞 `#Shorts`。
- description hashtag 最多 3 個高相關詞。
- `4K / 60fps / 直式` 只根據實際 ffprobe 結果寫入，不猜。
- 人名只有在檔名或**立即父資料夾名稱**明確包含設定的人名時才加入；不做人臉辨識。

例如：

```text
08/13/卡洛琳/video_20260813_183252.mp4
```

可以使用卡洛琳 metadata；單純：

```text
08/13/video_20260813_183252.mp4
```

仍使用通用啦啦隊 metadata。

## Media QC

每支新影片 upload 前執行：

- ffprobe 可讀性 / duration / resolution 檢查
- 5 點 average-hash fingerprint
- duration + perceptual Hamming distance duplicate detection
- 過暗 / 低對比 / 極低畫面變化 warning

`probable_duplicate` 或不可讀影片會標成 `qc_rejected`，**不刪原始 Drive 檔**。

## Thumbnail

Agent 會從影片多個時間點挑 exposure / contrast 較健康的 frame，best-effort 呼叫 YouTube thumbnail API。若該頻道或 Shorts 類型不允許 API 設定 thumbnail，會記錄結果後繼續上傳，不會讓正式 publisher 失敗。

## 防重複 / 當機恢復

最重要的邊界：

```text
YouTube API 回傳 video_id
        ↓
先寫 .YT_MEDIA_STATE.json
        ↓
才做後續 processing / Drive move
```

因此 runner 在 upload 成功後即使中斷，下一輪只會 reconcile 該 YouTube video，不會重新上傳同一支 Drive 檔案。

另外，新影片會保存 media fingerprint，避免內容重複但 Drive file ID 不同時被再次上傳。

## Secrets

GitHub Actions 使用：

```text
YT_MEDIA_CLIENT_SECRET_JSON
YT_MEDIA_DRIVE_TOKEN_JSON
YT_MEDIA_YOUTUBE_TOKEN_JSON
YT_MEDIA_YOUTUBE_MANAGE_TOKEN_JSON
```

由 `SETUP_GITHUB_ACTIONS.cmd` 從 `%LOCALAPPDATA%\YT_MEDIA` 直接寫入 GitHub Secrets。不要把 OAuth JSON 貼到 Issue、PR、README 或 commit。

## 排程長期存活

GitHub 對 public repo 若長期沒有 repository activity，可能自動停用 scheduled workflows。`keepalive.yml` 定期建立 `[skip ci]` 空 commit，避免 publisher 因 inactivity 被停掉。

## 本機工具

本機保留作 OAuth/bootstrap/emergency console：

```powershell
.\scripts\doctor.ps1
.\scripts\queue.ps1
.\RUN_NOW.cmd
```

Actions cutover 成功後，不再依賴 Windows Task Scheduler。

## 不可破壞的安全條件

- OAuth YouTube Channel ID 不等於 `UCzqapvxqSNMeNEM2ng91sow` 就拒絕執行。
- 已持久化 `youtube_video_id` 就不重新上傳。
- YouTube processing 成功後才移動 Drive 原片。
- 不刪除原始影片。
- `02_待剪輯`、`03_重複待刪除`、`04_已上傳` 永遠不掃描。
- Analytics / thumbnail 失敗不得阻止正常 publisher；只有 Drive/YouTube identity、安全 state、實際 upload failure 才能阻擋正式流程。
