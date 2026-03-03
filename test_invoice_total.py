#!/usr/bin/env python3
"""
測試腳本：抓取「前日開立發票總額」
條件：在過去 24 小時內，任務被移動到「售後服務(已開立發票、未收款)」區段，
      即 Asana 工作流程 1213428558979322 的觸發條件。
"""

import os
import requests
from datetime import datetime, timedelta, timezone

ASANA_TOKEN = os.environ.get("ASANA_TOKEN", "2/1212992285979119/1213372540895024:195163b8ffd852889ae0946828029e2b")
ASANA_BASE = "https://app.asana.com/api/1.0"
ASANA_PROJECT_GID = "1208705115282620"
ASANA_WORKSPACE_GID = "1202280977140605"

# 目標區段：售後服務(已開立發票、未收款)
TARGET_SECTION_GID = "1209970662747023"
TARGET_SECTION_NAME = "售後服務(已開立發票、未收款)"

# 實際成交金額(稅後) custom field GID
AMOUNT_FIELD_GID = "1211061298683016"

TW_TZ = timezone(timedelta(hours=8))


def asana_get(endpoint, params=None):
    headers = {"Authorization": f"Bearer {ASANA_TOKEN}"}
    url = f"{ASANA_BASE}/{endpoint}"
    all_data = []
    while True:
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        body = resp.json()
        all_data.extend(body.get("data", []))
        next_page = body.get("next_page")
        if next_page and next_page.get("offset"):
            if params is None:
                params = {}
            params = dict(params)
            params["offset"] = next_page["offset"]
        else:
            break
    return all_data


def get_task_details(task_gid):
    """取得任務詳細資訊，包含 custom fields"""
    params = {
        "opt_fields": f"name,custom_fields.gid,custom_fields.number_value,custom_fields.name,memberships.section.name,memberships.section.gid"
    }
    headers = {"Authorization": f"Bearer {ASANA_TOKEN}"}
    resp = requests.get(f"{ASANA_BASE}/tasks/{task_gid}", headers=headers, params=params)
    resp.raise_for_status()
    return resp.json().get("data", {})


def get_task_stories(task_gid):
    params = {"opt_fields": "created_at,resource_subtype,text,created_by.name,type"}
    return asana_get(f"tasks/{task_gid}/stories", params)


def main():
    now_utc = datetime.now(timezone.utc)
    until_utc = now_utc
    since_utc = until_utc - timedelta(hours=24)

    since_tw = since_utc.astimezone(TW_TZ)
    until_tw = until_utc.astimezone(TW_TZ)

    since_iso = since_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    until_iso = until_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    print(f"📅 時間範圍: {since_tw.strftime('%Y/%m/%d %H:%M')} ~ {until_tw.strftime('%Y/%m/%d %H:%M')} (台灣時間)")
    print(f"🎯 目標區段: {TARGET_SECTION_NAME}")
    print()

    # 1. 搜尋修改過的任務
    print("🔍 搜尋修改過的任務...")
    params = {
        "modified_at.after": since_iso,
        "modified_at.before": until_iso,
        "projects.any": ASANA_PROJECT_GID,
        "opt_fields": "name,modified_at",
        "limit": 100,
    }
    modified_tasks = asana_get(f"workspaces/{ASANA_WORKSPACE_GID}/tasks/search", params)
    print(f"   找到 {len(modified_tasks)} 個修改過的任務\n")

    # 2. 逐一查 stories，找到移動到目標區段的任務
    matched_tasks = []

    for i, task in enumerate(modified_tasks):
        task_gid = task["gid"]
        task_name = task.get("name", "未命名")

        try:
            stories = get_task_stories(task_gid)
        except Exception as e:
            print(f"   ⚠️ 跳過 {task_name}（取得 stories 失敗）: {e}")
            continue

        for story in stories:
            created = story.get("created_at", "")
            if not (since_iso <= created <= until_iso):
                continue

            subtype = story.get("resource_subtype", "")
            text = story.get("text", "")

            # 偵測任何與目標區段相關的移動（移入或移出皆計算）
            if subtype == "section_changed" and TARGET_SECTION_NAME in text and "購案商機" in text:
                print(f"   ✅ [{i+1}] 移動到目標區段: {task_name}")
                print(f"      story: {text}")

                # 取得任務的 實際成交金額(稅後)
                try:
                    details = get_task_details(task_gid)
                    amount = None
                    for cf in details.get("custom_fields", []):
                        if cf.get("gid") == AMOUNT_FIELD_GID:
                            amount = cf.get("number_value")
                            break
                    print(f"      實際成交金額(稅後): {amount}")
                    matched_tasks.append({
                        "gid": task_gid,
                        "name": task_name,
                        "amount": amount,
                        "moved_at": created,
                    })
                except Exception as e:
                    print(f"      ⚠️ 取得金額失敗: {e}")
                break  # 同一任務只算一次

    # 3. 統計結果
    print()
    print("=" * 60)
    print(f"📊 結果：共 {len(matched_tasks)} 筆在此時段內移動到「{TARGET_SECTION_NAME}」")
    print()

    total = 0
    for t in matched_tasks:
        amt = t["amount"]
        amt_str = f"NT$ {amt:,.0f}" if amt is not None else "（未填寫）"
        moved_tw = datetime.fromisoformat(t["moved_at"].replace("Z", "+00:00")).astimezone(TW_TZ)
        print(f"  • {t['name']}")
        print(f"    移動時間：{moved_tw.strftime('%Y/%m/%d %H:%M')}")
        print(f"    實際成交金額(稅後)：{amt_str}")
        if amt is not None:
            total += amt

    print()
    if matched_tasks:
        print(f"💰 前日開立發票總額（實際成交金額稅後合計）：NT$ {total:,.0f}")
    else:
        print("此時段內無任務移動到目標區段")
    print("=" * 60)


if __name__ == "__main__":
    main()
