# YT_MEDIA — Google Drive → YouTube 自動成長系統

日常目標只有一個：**把影片丟進 Google Drive，電腦可以關機。**

```text
Google Drive「建發用」
        ↓
GitHub Actions（每小時第 17 分）
        ↓
Analytics feedback / staged experiment / media QC
        ↓
Drive 掃描 / FFmpeg / YouTube API
        ↓
YouTube「象兒應援團」排程發布
        ↓
1h / 6h / 24h / 72h / 7d exact snapshot
+ 24h / 72h / 7d YouTube Analytics
        ↓
Growth Dashboard / contextual learner
        ↺
下一批策略
```

正式 runtime 不需要 Google Cloud Billing。Cloud Run / Cloud Scheduler / Secret Manager / Cloud Storage 已退出正式架構。

## 已綁定環境

- Google Drive 根目錄：`建發用`
- Drive Folder ID：`1vg-sHZfam52sAZWqMu6uIHhUUqSZEC8x`
- YouTube：`象兒應援團`
- Channel ID：`UCzqapvxqSNMeNEM2ng91sow`
- 每輪最多：8 支
- 每個日期最多安排一支
- Persistent state：Google Drive 根目錄 `.YT_MEDIA_STATE.json`
- Production workflow：`.github/workflows/publish.yml`

## 日常使用

只做：

```text
影片 → Google Drive「建發用」底下日期資料夾
```

掃描規則：

```text
01_優先上傳      → 優先
一般日期資料夾    → 處理
02_待剪輯        → 忽略
03_重複待刪除    → 忽略
04_已上傳        → 忽略
```

若知道人物，可以明確用資料夾或檔名：

```text
08/13/卡洛琳/video_20260813_183252.mp4
```

只會使用設定中存在且文字明確出現的人名；**不做人臉辨識、不猜真人身份**。

## 一次性 OAuth / GitHub Actions 設定

Windows 執行：

```powershell
cd C:\YT_MEDIA
git pull --ff-only origin main
.\SETUP_GITHUB_ACTIONS.cmd
```

腳本會：

1. 驗證 Drive + YouTube upload OAuth。
2. 從 `client_secret.json` 取得 OAuth project ID；若本機有已登入的 `gcloud`，best-effort 啟用 `youtubeanalytics.googleapis.com`。失敗不會阻擋 publisher。
3. 驗證 management token **實際儲存的 scopes** 必須同時包含：
   - `https://www.googleapis.com/auth/youtube`
   - `https://www.googleapis.com/auth/yt-analytics.readonly`
4. 舊 token 缺 Analytics scope 時自動要求重新授權；選擇 Brand Account `象兒應援團`。
5. 安全合併本機 state 到 Drive `.YT_MEDIA_STATE.json`。
6. 將 OAuth JSON 直接寫入 GitHub repository secrets，不 commit 到 repo。
7. 觸發 production workflow 做 end-to-end 驗收。

GitHub Secrets：

```text
YT_MEDIA_CLIENT_SECRET_JSON
YT_MEDIA_DRIVE_TOKEN_JSON
YT_MEDIA_YOUTUBE_TOKEN_JSON
YT_MEDIA_YOUTUBE_MANAGE_TOKEN_JSON
```

不要把 OAuth JSON 放進 Issue、PR、README 或 commit。

## Production workflow

```text
Verify Drive + target YouTube channel
        ↓
Collect exact snapshots + YouTube Analytics
        ↓
Update strategy
        ↓
Refresh unpublished metadata
        ↓
Scan Drive
        ↓
Media QC + duplicate detection + opening-second measurement
        ↓
Choose experiment arm
        ↓
Upload
        ↓
Persist youtube_video_id immediately
        ↓
Best-effort thumbnail（若該 phase/arm 選到）
        ↓
YouTube processing success
        ↓
Move source to 04_已上傳
        ↓
Generate Growth Dashboard artifact
```

Workflow 使用 `concurrency`，同一時間只允許一個 publisher，避免兩輪處理同一批影片。

## 學習資料：兩種時間口徑嚴格分開

### Exact cumulative snapshots

YouTube Data API 在發布後跨過以下門檻時保存當下累積值：

```text
1h / 6h / 24h / 72h / 7d
```

主要保存 views / likes / comments，標記：

```text
time_basis = cumulative_at_capture
training_eligible = false
```

這些資料適合看 early velocity，但**不拿來訓練 bandit**，避免把不完整數據當 retention/engagement 真相。

### YouTube Analytics learning windows

Analytics 使用獨立資料區：

```text
24h / 72h / 7d
```

它是 YouTube Analytics 的日期型查詢，state 明確標記：

```text
time_basis = calendar_date_query
source = youtube_analytics
training_eligible = true
```

只有 `source=youtube_analytics` 的成熟 snapshot 可以影響策略。

收集訊號包括：

- views / engagedViews
- averageViewDuration / averageViewPercentage
- likes / comments / shares
- subscribersGained
- `insightTrafficSourceType`，包含 Shorts vertical experience 的 `SHORTS` traffic
- retention curve：`elapsedVideoTimeRatio` + `audienceWatchRatio` + `relativeRetentionPerformance`

Retention 會摘要 1% / 10% / 25% / 50% / 75% / 100% checkpoint，方便判斷開頭掉人與完成度。

## 評分不是 YouTube 私有公式

這是 channel-local optimization score：

```text
Reach       20%
Retention   45%
Engagement  20%
Conversion  15%
```

Dashboard 同時保留四個分數，不只給 Total Score，因此可以區分：

- 平台沒有給足 reach
- 有 reach 但 retention 差
- 有觀看但互動弱
- 有觀看但訂閱轉換弱

## Contextual learner

每支影片會保存 context：

```text
person / generic
duration bucket
4K / 其他畫質
60 / 30 fps
vertical / horizontal
opening = active / static / dark / unknown
weekday / weekend
```

策略比較不再把所有影片混成一鍋。與新片 context 越相似的歷史樣本，權重越高；這降低「剛好熱門人物分到某個標題，所以誤以為標題是原因」的 confounding。

## 實驗分階段，不同時亂改所有變數

目前 plan：

```text
Phase 1: metadata
  24 個成熟 72h 樣本
        ↓
Phase 2: publish_time
  24 個成熟 72h 樣本
        ↓
Phase 3: thumbnail
  16 個成熟 72h 樣本
        ↓
Exploit / champion
```

Phase 1 只探索 metadata；發布時間與 thumbnail 使用目前 champion/default。進 Phase 2 後 metadata 固定 champion，只測發布時間；Phase 3 同理。

Metadata arms：

```text
direct / stadium / live / quality / moment / energy
```

Publish-time arms：

```text
17:30 / 18:30 / 19:30 / 20:30 / 21:30
```

Thumbnail arms：

```text
best_frame / youtube_default
```

Bandit 保留 exploration，但同一 Drive file ID 的 assignment 可重現，不會因 retry 隨機換組。

## Media QC / 內容特徵

新影片 upload 前：

- ffprobe 可讀性 / duration / resolution
- 多時間點 perceptual fingerprint
- duration + Hamming distance near-duplicate detection
- 過暗 / 低對比 / 極低畫面變化 warning
- opening-second brightness / motion measurement
- `first_second_likely_dead_air` warning

`probable_duplicate` 或不可讀影片標記 `qc_rejected`，**原始 Drive 檔不刪除**。

目前 opening-second 只測量，不自動剪片；避免在沒有 retention 證據前做不可逆修改。

## Thumbnail

`best_frame` 會從多個時間點以曝光與對比挑 frame，再 best-effort 呼叫 YouTube thumbnail API。若 YouTube/Shorts 不允許 API 設 thumbnail，記錄結果後繼續 publisher。

`youtube_default` 完全交給 YouTube。

## Growth Dashboard

每輪 workflow 最後產生：

```text
growth_dashboard.json
growth_dashboard.md
```

並上傳成 GitHub Actions artifact：

```text
yt-media-growth-dashboard
```

保存 30 天。Dashboard 顯示：

- active experiment + mature sample progress
- 最近 7 天 snapshot totals
- metadata / publish-time / thumbnail leaderboard
- Top / Bottom videos
- opening / duration / person / weekday 的平均 score correlations

## Metadata 原則

- 搜尋關鍵字靠前。
- 不在標題硬塞 `#Shorts`。
- description hashtag 最多 3 個高相關詞。
- `4K / 60fps / 直式` 只根據 ffprobe 實際資料。
- 人名只來自明確檔名/資料夾文字。

## 防重複 / crash recovery

不可破壞的邊界：

```text
YouTube API 回傳 video_id
        ↓
先寫 .YT_MEDIA_STATE.json
        ↓
才做 processing / Drive move
```

因此 runner 在 upload 成功後即使中斷，下一輪只 reconcile 已存在的 YouTube video，不重新 upload。

另外保存 media fingerprint，防止同內容換了不同 Drive file ID 又被上傳。

## Fail-open / Fail-closed

可以 fail-open：

- Analytics
- metadata refresh
- thumbnail
- dashboard

必須 fail-closed：

- Drive OAuth / root identity
- YouTube target channel identity
- durable state
- duplicate safety boundary
- actual upload failure

Analytics 掛掉不應讓影片停更；但也**絕不能拿 fallback 假裝完整 Analytics 繼續學**。

## 排程長期存活

Public repo 長期無 activity 可能影響 scheduled workflows；`keepalive.yml` 定期建立 `[skip ci]` activity，避免 publisher 因 inactivity 停用。

## 本機工具

```powershell
.\scripts\doctor.ps1
.\scripts\queue.ps1
.\RUN_NOW.cmd
```

GitHub Actions 上線後不依賴 Windows Task Scheduler。
