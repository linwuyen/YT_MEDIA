# YT_MEDIA — Google Drive → YouTube 全自動發布

目標只有一個：**日常使用時，你只負責把影片丟進 Google Drive。**

Windows Agent 會自動：掃描影片 → 下載 → FFmpeg 清理 → 產生標題/說明 → 上傳 YouTube → 排程每天 18:30 → 成功後移到 `04_已上傳`。

## 已綁定的環境

- Google Drive 根目錄：`建發用`
- Drive Folder ID：`1vg-sHZfam52sAZWqMu6uIHhUUqSZEC8x`
- YouTube 頻道：`象兒應援團`
- Channel ID：`UCzqapvxqSNMeNEM2ng91sow`
- 預設發布時間：每天 `18:30`（Asia/Taipei）

## 你之後怎麼用

只做這件事：

```text
手機/相機影片 → Google Drive「建發用」底下任一日期資料夾
```

Agent 會遞迴掃描。它會優先處理 `01_優先上傳`，並完全忽略：

```text
02_待剪輯
03_重複待刪除
04_已上傳
```

如果日期資料夾沒有 `01_優先上傳`，直接放在日期資料夾裡的 MP4 也會被處理。

## 第一次只做一次

在 Windows PowerShell：

```powershell
git clone https://github.com/linwuyen/YT_MEDIA.git C:\YT_MEDIA
cd C:\YT_MEDIA
Set-ExecutionPolicy -Scope Process Bypass -Force
.\INSTALL.cmd
```

`INSTALL.cmd` / `scripts/bootstrap.ps1` 會：

1. 建立 Python venv。
2. 安裝 Python 套件。
3. 嘗試安裝 FFmpeg（沒有也能跑，只是不做 stream cleanup）。
4. 從 Downloads 自動尋找 `client_secret*.json`。
5. 開瀏覽器做一次 Drive OAuth。
6. 開瀏覽器做一次 YouTube OAuth。
7. **嚴格驗證 OAuth 頻道一定是 `UCzqapvxqSNMeNEM2ng91sow`。**
8. 建立 Windows 工作排程 `YT_MEDIA_AutoPublisher`。

若 YouTube OAuth 仍回傳你的個人頻道，bootstrap 會直接失敗，不會上傳任何影片。

## 自動執行

安裝完成後：

- Windows 登入時跑一次。
- 之後每小時跑一次。
- 每次開始先 `git pull --ff-only origin main`，所以 GitHub 更新會自動同步到 Agent。
- 同時只允許一個 Agent instance，避免重複上傳。

## 手動檢查

```powershell
.\scripts\doctor.ps1
.\scripts\queue.ps1
.\RUN_NOW.cmd
```

## 防重複/當機恢復

Runtime 全部放在：

```text
%LOCALAPPDATA%\YT_MEDIA
```

其中：

```text
client_secret.json
drive_token.json
youtube_token.json
state.json
logs\agent.jsonl
work\
```

一旦 YouTube 回傳 `video_id`，Agent 會先寫入 `state.json`。就算電腦在「上傳成功 → 移動 Drive」之間當機，下次也只會檢查該 YouTube 影片狀態，不會重新上傳。

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

Agent 不會亂猜真人身分，而是用通用應援標題。人物清單與標題模板都在 `config/default.json`，以後改 GitHub 即可。

## GitHub 與 Windows 的分工

```text
GitHub YT_MEDIA
  程式 / 設定 / 測試 / CI
          ↓ git pull
Windows Agent
  OAuth token / FFmpeg / runtime state
          ↓
Google Drive → YouTube
```

影片與 OAuth token **永遠不進 GitHub**。

## CI

每個 PR 會在 Python 3.11 / 3.12 執行：

- compileall
- pytest

## 安全設計

- OAuth 頻道 ID 不符，拒絕上傳。
- YouTube 已回傳 `video_id` 的 Drive 檔案不會再次 upload。
- YouTube processing 成功後才移動 Drive 原片。
- 不刪除原始影片。
- `02_待剪輯`、`03_重複待刪除`、`04_已上傳` 永遠不掃描。
