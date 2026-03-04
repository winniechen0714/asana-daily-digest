# Asana 購案商機 — 每日更新摘要

每日台灣時間 08:00 自動從 Asana「購案商機」專案抓取過去 24 小時的變動，透過 Slack Incoming Webhook 發送摘要到 `#購案商機每日更新` 頻道。

## 追蹤內容

| 類別 | 說明 |
|------|------|
| 💰 前日開立發票總額 | 偵測「售後服務(已開立發票、未收款)」區段有移動的任務（移入或移出），加總其「實際成交金額(稅後)」欄位，對應 Asana 工作流程 `1213428558979322` 的觸發條件 |
| 🆕 新建立的任務 | 新建的 Task（排除子任務），依建立者姓名排序 |
| 🔄 任務階段移動 | Section 變更紀錄（僅限購案商機專案，不含開立發票區段） |
| 💬 新評論 | 任務內新增的人工評論（排除系統自動化評論）；若為子任務，標題顯示為「母任務 > 子任務」 |

> 所有任務名稱均為 Slack 可點擊連結，點擊後直接開啟對應 Asana 任務。

## 設定 GitHub Secrets

到 Repository → Settings → Secrets and variables → Actions，新增：

| Secret 名稱 | 說明 |
|-------------|------|
| `ASANA_TOKEN` | Asana Personal Access Token |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL |

## 手動測試

GitHub → Actions → 選擇 workflow → Run workflow

或使用測試腳本（需設定環境變數）：

```bash
export ASANA_TOKEN=your_token
python test_invoice_total.py   # 測試前日開立發票邏輯
```

## 排程

- Cron: `0 22 * * *`（UTC 22:00 = 台灣 06:00）
- 掃描範圍：固定為台灣時間「昨日 06:00 ~ 今日 06:00」，不受 cron 實際觸發時間影響
- 改為 UTC 22:00 觸發以避開 UTC 00:00 的 GitHub Actions 排程高峰，降低延遲
