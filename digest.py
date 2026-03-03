#!/usr/bin/env python3
"""
Asana 購案商機 — 每日更新摘要
每日台灣時間 08:00 自動抓取過去 24 小時的專案變動，並透過 Webhook 發送到 Slack。
"""

import os
import json
import requests
from datetime import datetime, timedelta, timezone

# ===== 設定 =====
ASANA_TOKEN = os.environ["ASANA_TOKEN"]
SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]

ASANA_PROJECT_GID = "1208705115282620"  # 購案商機
ASANA_WORKSPACE_GID = "1202280977140605"  # 思邁智能股份有限公司

ASANA_BASE = "https://app.asana.com/api/1.0"

# 台灣時區 UTC+8
TW_TZ = timezone(timedelta(hours=8))


def asana_get(endpoint, params=None):
    """發送 Asana API GET 請求（含分頁處理）"""
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
            params["offset"] = next_page["offset"]
        else:
            break
    return all_data


def search_new_tasks(since_utc, until_utc):
    """搜尋時間範圍內新建立的任務（排除子任務）"""
    params = {
        "created_at.after": since_utc,
        "created_at.before": until_utc,
        "projects.any": ASANA_PROJECT_GID,
        "is_subtask": False,
        "opt_fields": "name,created_at,created_by.name,memberships.section.name",
        "limit": 100,
    }
    return asana_get(f"workspaces/{ASANA_WORKSPACE_GID}/tasks/search", params)


def search_modified_tasks(since_utc, until_utc):
    """搜尋時間範圍內有修改的任務"""
    params = {
        "modified_at.after": since_utc,
        "modified_at.before": until_utc,
        "projects.any": ASANA_PROJECT_GID,
        "opt_fields": "name,modified_at",
        "limit": 100,
    }
    return asana_get(f"workspaces/{ASANA_WORKSPACE_GID}/tasks/search", params)


def get_task_stories(task_gid):
    """取得任務的活動紀錄"""
    params = {
        "opt_fields": "created_at,resource_subtype,text,created_by.name,type",
    }
    return asana_get(f"tasks/{task_gid}/stories", params)


def filter_stories_in_range(stories, since_utc, until_utc):
    """篩選時間範圍內購案商機專案的 section_changed 和 comment_added"""
    section_changes = []
    comments = []

    for story in stories:
        created = story.get("created_at", "")
        if not (since_utc <= created <= until_utc):
            continue

        subtype = story.get("resource_subtype", "")
        text = story.get("text", "")
        creator = story.get("created_by", {})
        creator_name = creator.get("name", "系統") if creator else "系統"

        if subtype == "section_changed" and "購案商機" in text:
            section_changes.append({
                "text": text,
                "creator": creator_name,
            })
        elif subtype == "comment_added":
            comments.append({
                "text": text,
                "creator": creator_name,
            })

    return section_changes, comments


def parse_section_change(text):
    """解析 section_changed 文字，提取原階段和新階段"""
    try:
        from_part = text.split('from "')[1].split('" to "')[0]
        to_part = text.split('" to "')[1].split('" in ')[0]
        return from_part, to_part
    except (IndexError, ValueError):
        return None, None


def build_message(new_tasks, section_moves, new_comments, since_tw, until_tw):
    """組合 Slack 訊息"""
    weekdays = ["一", "二", "三", "四", "五", "六", "日"]
    since_str = f"{since_tw.strftime('%Y/%m/%d')} ({weekdays[since_tw.weekday()]}) {since_tw.strftime('%H:%M')}"
    until_str = f"{until_tw.strftime('%Y/%m/%d')} ({weekdays[until_tw.weekday()]}) {until_tw.strftime('%H:%M')}"

    lines = []
    lines.append("📋 購案商機 — 每日更新摘要")
    lines.append(f"📅 {since_str} ~ {until_str} (台灣時間)")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 新建任務
    lines.append("▶ 【新建立的任務】")
    lines.append("")
    if new_tasks:
        for task in new_tasks:
            name = task.get("name", "未命名")
            creator = task.get("creator", "未知")
            section = task.get("section", "")
            line = f"• {name} — 建立者：{creator}"
            if section:
                line += f"\n  目前階段：{section}"
            lines.append(line)
    else:
        lines.append("此時段內無新建立的任務")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 階段移動
    lines.append("▶ 【任務階段移動】")
    lines.append("")
    if section_moves:
        for move in section_moves:
            name = move["task_name"]
            from_section = move["from_section"]
            to_section = move["to_section"]
            creator = move["creator"]
            lines.append(f"• {name}")
            lines.append(f"  `{from_section}` → `{to_section}`（操作者：{creator}）")
            lines.append("")
    else:
        lines.append("此時段內無任務階段移動")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 新評論
    lines.append("▶ 【新評論】")
    lines.append("")
    if new_comments:
        for comment in new_comments:
            name = comment["task_name"]
            creator = comment["creator"]
            text = comment["text"]
            if len(text) > 200:
                text = text[:200] + "..."
            lines.append(f"• {name} — {creator}")
            lines.append(f"  「{text}」")
            lines.append("")
    else:
        lines.append("此時段內無新增評論")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("此摘要由 Claude Agent 自動產生，資料來源為 Asana「購案商機」專案活動紀錄。")

    return "\n".join(lines)


def send_to_slack(message):
    """透過 Incoming Webhook 發送訊息到 Slack"""
    payload = {"text": message}
    resp = requests.post(SLACK_WEBHOOK_URL, json=payload)
    resp.raise_for_status()
    if resp.text != "ok":
        raise RuntimeError(f"Slack Webhook 錯誤: {resp.text}")
    print("✅ 訊息已透過 Webhook 發送到 Slack")
    return resp


def main():
    # 計算時間範圍：過去 24 小時
    now_utc = datetime.now(timezone.utc)
    until_utc = now_utc
    since_utc = until_utc - timedelta(hours=24)

    # 台灣時間（用於顯示）
    since_tw = since_utc.astimezone(TW_TZ)
    until_tw = until_utc.astimezone(TW_TZ)

    since_iso = since_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    until_iso = until_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    print(f"📅 時間範圍: {since_tw.strftime('%Y/%m/%d %H:%M')} ~ {until_tw.strftime('%Y/%m/%d %H:%M')} (台灣時間)")
    print(f"   UTC: {since_iso} ~ {until_iso}")

    # 1. 搜尋新建立的任務
    print("\n🔍 搜尋新建立的任務...")
    raw_new_tasks = search_new_tasks(since_iso, until_iso)
    new_tasks = []
    for task in raw_new_tasks:
        creator = task.get("created_by", {})
        creator_name = creator.get("name", "未知") if creator else "未知"
        section = ""
        memberships = task.get("memberships", [])
        for m in memberships:
            sec = m.get("section", {})
            if sec:
                section = sec.get("name", "")
                break
        new_tasks.append({
            "name": task.get("name", "未命名"),
            "creator": creator_name,
            "section": section,
        })
    print(f"   找到 {len(new_tasks)} 個新任務")

    # 2. 搜尋有修改的任務，逐一查 stories
    print("\n🔍 搜尋修改過的任務...")
    modified_tasks = search_modified_tasks(since_iso, until_iso)
    print(f"   找到 {len(modified_tasks)} 個修改過的任務")

    all_section_moves = []
    all_comments = []

    for i, task in enumerate(modified_tasks):
        task_gid = task["gid"]
        task_name = task.get("name", "未命名")
        print(f"   [{i+1}/{len(modified_tasks)}] 檢查: {task_name}")

        try:
            stories = get_task_stories(task_gid)
        except Exception as e:
            print(f"   ⚠️ 跳過（取得 stories 失敗）: {e}")
            continue

        section_changes, comments = filter_stories_in_range(stories, since_iso, until_iso)

        for sc in section_changes:
            from_sec, to_sec = parse_section_change(sc["text"])
            if from_sec and to_sec:
                all_section_moves.append({
                    "task_name": task_name,
                    "from_section": from_sec,
                    "to_section": to_sec,
                    "creator": sc["creator"],
                })

        for c in comments:
            all_comments.append({
                "task_name": task_name,
                "text": c["text"],
                "creator": c["creator"],
            })

    print(f"\n📊 統計:")
    print(f"   新建任務: {len(new_tasks)}")
    print(f"   階段移動: {len(all_section_moves)}")
    print(f"   新評論: {len(all_comments)}")

    # 3. 組合訊息
    message = build_message(new_tasks, all_section_moves, all_comments, since_tw, until_tw)

    # 4. 發送到 Slack
    print("\n📤 發送到 Slack...")
    send_to_slack(message)
    print("\n✅ 完成！")


if __name__ == "__main__":
    main()
