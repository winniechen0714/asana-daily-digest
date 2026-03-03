# Asana 購案商機 — 每日更新摘要

每日台灣時間 08:00 自動從 Asana「購案商機」專案抓取過去 24 小時的變動，透過 Slack Incoming Webhook 發送摘要到 `#購案商機每日更新` 頻道。

## 追蹤內容

| 類別 | 說明 |
|------|------|
| 🆕 新建立的任務 | 新建的 Task（排除子任務） |
| 🔄 任務階段移動 | Section 變更紀錄（僅限購案商機專案） |
| 💬 新評論 | 任務內新增的評論 |

## 設定 GitHub Secrets

到 Repository → Settings → Secrets and variables → Actions，新增：

| Secret 名稱 | 說明 |
|-------------|------|
| `ASANA_TOKEN` | Asana Personal Access Token |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL |

## 手動測試

GitHub → Actions → 選擇 workflow → Run workflow

## 排程

- Cron: `0 0 * * *`（UTC 00:00 = 台灣 08:00）
- 掃描範圍：執行時間往前推 24 小時
