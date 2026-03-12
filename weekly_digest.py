#!/usr/bin/env python3
"""
Asana 購案商機 — 每週更新摘要
每週一台灣時間 08:00 自動執行，包含：
  1. 本週開立發票金額統計
  2. 本週任務階段移動
  3. 續約聯絡期提醒（環境到期日在 3 個月內的未完成任務）
  4. 超過 30 天未異動的停滯任務
"""

import os
import requests
from datetime import datetime, timedelta, timezone, date

# ===== 設定 =====
ASANA_TOKEN = os.environ["ASANA_TOKEN"]
SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]

ASANA_PROJECT_GID = "1208705115282620"      # 購案商機
ASANA_WORKSPACE_GID = "1202280977140605"    # 思邁智能股份有限公司

ASANA_BASE = "https://app.asana.com/api/1.0"

TW_TZ = timezone(timedelta(hours=8))
STALE_DAYS = 30          # 超過幾天沒異動視為停滯
RENEWAL_MONTHS = 3       # 幾個月內到期視為續約聯絡期

INVOICE_SECTION_NAME = "售後服務(已開立發票、未收款)"
PAYMENT_SECTION_NAME = "已開發票、且已收款(售後須追蹤方案到期日)"
AMOUNT_FIELD_GID = "1211061298683016"       # 實際成交金額(稅後)
EXPIRY_FIELD_NAME = "環境到期日"            # 用名稱動態查找 GID
PRODUCT_FIELD_NAME = "購買商品"            # 用名稱動態查找 GID
ADDON_SERVICE_VALUES = {                   # 加購服務不列入續約聯絡期
    "字數加購",
    "Web Chat 公版開發授權",
    "容量加購",
    "線上教育訓練",
}

# 停滯任務只追蹤這些區段（其餘皆忽略）
STALE_WATCH_SECTIONS = {
    "商機已聯繫未回應",
    "商機確認中",
    "需求釐清",
    "初次介紹",
    "需求評估",
    "提供初步報價",
    "簽署合意向書或保密協定",
    "POC",
    "最終報價",
    "等待訂單",
    "售後服務(已開立發票、未收款)",
}


# ===== 共用工具 =====

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
            params = dict(params or {})
            params["offset"] = next_page["offset"]
        else:
            break
    return all_data


def task_url(task_gid):
    return f"https://app.asana.com/0/{ASANA_PROJECT_GID}/{task_gid}"


def slack_link(url, text):
    return f"<{url}|{text}>"


# ===== 取得專案自訂欄位 GID =====

def get_expiry_field_gid():
    """動態查找「環境到期日」custom field 的 GID"""
    headers = {"Authorization": f"Bearer {ASANA_TOKEN}"}
    params = {"opt_fields": "custom_field_settings.custom_field.gid,custom_field_settings.custom_field.name"}
    resp = requests.get(f"{ASANA_BASE}/projects/{ASANA_PROJECT_GID}", headers=headers, params=params)
    resp.raise_for_status()
    settings = resp.json().get("data", {}).get("custom_field_settings", [])
    for s in settings:
        cf = s.get("custom_field", {})
        if cf.get("name") == EXPIRY_FIELD_NAME:
            return cf.get("gid")
    return None


def get_product_field_gid():
    """動態查找「購買商品」custom field 的 GID"""
    headers = {"Authorization": f"Bearer {ASANA_TOKEN}"}
    params = {"opt_fields": "custom_field_settings.custom_field.gid,custom_field_settings.custom_field.name"}
    resp = requests.get(f"{ASANA_BASE}/projects/{ASANA_PROJECT_GID}", headers=headers, params=params)
    resp.raise_for_status()
    settings = resp.json().get("data", {}).get("custom_field_settings", [])
    for s in settings:
        cf = s.get("custom_field", {})
        if cf.get("name") == PRODUCT_FIELD_NAME:
            return cf.get("gid")
    return None


# ===== 本週開立發票 =====

def get_invoice_section_gid():
    """取得發票區段的 GID"""
    sections = asana_get(f"projects/{ASANA_PROJECT_GID}/sections", {
        "opt_fields": "name",
    })
    for s in sections:
        if s.get("name") == INVOICE_SECTION_NAME:
            return s.get("gid")
    return None


def get_weekly_invoice_tasks(since_iso, until_iso):
    """直接抓發票區段的任務，再確認本週是否有移入紀錄"""
    section_gid = get_invoice_section_gid()
    if not section_gid:
        print(f"   ⚠️ 找不到發票區段：{INVOICE_SECTION_NAME}")
        return [], 0

    # 取得目前在發票區段的所有任務
    section_tasks = asana_get(f"sections/{section_gid}/tasks", {
        "opt_fields": "name",
        "limit": 100,
    })
    print(f"   發票區段目前有 {len(section_tasks)} 筆任務")

    invoice_tasks = []
    invoice_total = 0

    for task in section_tasks:
        task_gid = task["gid"]
        task_name = task.get("name", "未命名")
        try:
            stories = asana_get(f"tasks/{task_gid}/stories", {
                "opt_fields": "created_at,resource_subtype,text",
            })
        except Exception:
            continue

        # 確認本週是否有移入發票區段的紀錄
        has_invoice_move = False
        for story in stories:
            if story.get("resource_subtype") != "section_changed":
                continue
            created = story.get("created_at", "")
            text = story.get("text", "")
            if since_iso <= created <= until_iso and INVOICE_SECTION_NAME in text:
                has_invoice_move = True
                print(f"   ✅ 本週移入: {task_name}")
                break

        if not has_invoice_move:
            continue

        # 取金額
        try:
            headers = {"Authorization": f"Bearer {ASANA_TOKEN}"}
            r = requests.get(
                f"{ASANA_BASE}/tasks/{task_gid}",
                headers=headers,
                params={"opt_fields": f"custom_fields.gid,custom_fields.number_value"},
            )
            r.raise_for_status()
            amount = None
            for cf in r.json().get("data", {}).get("custom_fields", []):
                if cf.get("gid") == AMOUNT_FIELD_GID:
                    amount = cf.get("number_value")
                    break
        except Exception:
            amount = None

        invoice_tasks.append({"task_gid": task_gid, "name": task_name, "amount": amount})
        if amount is not None:
            invoice_total += amount

    return invoice_tasks, invoice_total


# ===== 本週已收款 =====

def get_weekly_payment_tasks(since_iso, until_iso):
    """直接抓已收款區段的任務，再確認本週是否有移入紀錄"""
    sections = asana_get(f"projects/{ASANA_PROJECT_GID}/sections", {"opt_fields": "name"})
    section_gid = next((s["gid"] for s in sections if s.get("name") == PAYMENT_SECTION_NAME), None)
    if not section_gid:
        print(f"   ⚠️ 找不到已收款區段：{PAYMENT_SECTION_NAME}")
        return [], 0

    section_tasks = asana_get(f"sections/{section_gid}/tasks", {"opt_fields": "name", "limit": 100})
    print(f"   已收款區段目前有 {len(section_tasks)} 筆任務")

    payment_tasks = []
    payment_total = 0

    for task in section_tasks:
        task_gid = task["gid"]
        task_name = task.get("name", "未命名")
        try:
            stories = asana_get(f"tasks/{task_gid}/stories", {
                "opt_fields": "created_at,resource_subtype,text",
            })
        except Exception:
            continue

        has_payment_move = False
        for story in stories:
            if story.get("resource_subtype") != "section_changed":
                continue
            created = story.get("created_at", "")
            text = story.get("text", "")
            if since_iso <= created <= until_iso and PAYMENT_SECTION_NAME in text:
                has_payment_move = True
                print(f"   ✅ 本週移入: {task_name}")
                break

        if not has_payment_move:
            continue

        try:
            headers = {"Authorization": f"Bearer {ASANA_TOKEN}"}
            r = requests.get(
                f"{ASANA_BASE}/tasks/{task_gid}",
                headers=headers,
                params={"opt_fields": "custom_fields.gid,custom_fields.number_value"},
            )
            r.raise_for_status()
            amount = None
            for cf in r.json().get("data", {}).get("custom_fields", []):
                if cf.get("gid") == AMOUNT_FIELD_GID:
                    amount = cf.get("number_value")
                    break
        except Exception:
            amount = None

        payment_tasks.append({"task_gid": task_gid, "name": task_name, "amount": amount})
        if amount is not None:
            payment_total += amount

    return payment_tasks, payment_total


# ===== 階段移動 =====

def get_section_moves(since_iso, until_iso):
    """找出本週有 section_changed 的任務（排除發票區段）"""
    params = {
        "modified_at.after": since_iso,
        "modified_at.before": until_iso,
        "projects.any": ASANA_PROJECT_GID,
        "opt_fields": "name,modified_at",
        "limit": 100,
    }
    tasks = asana_get(f"workspaces/{ASANA_WORKSPACE_GID}/tasks/search", params)

    moves = []
    for task in tasks:
        task_gid = task["gid"]
        task_name = task.get("name", "未命名")
        try:
            stories = asana_get(f"tasks/{task_gid}/stories", {
                "opt_fields": "created_at,resource_subtype,text,created_by.name",
            })
        except Exception:
            continue

        for story in stories:
            if story.get("resource_subtype") != "section_changed":
                continue
            created = story.get("created_at", "")
            if not (since_iso <= created <= until_iso):
                continue
            text = story.get("text", "")
            if "購案商機" not in text:
                continue
            if INVOICE_SECTION_NAME in text:
                continue  # 發票區段移動另外統計
            try:
                from_sec = text.split('from "')[1].split('" to "')[0]
                to_sec = text.split('" to "')[1].split('" in ')[0]
            except (IndexError, ValueError):
                continue
            creator = story.get("created_by", {})
            moves.append({
                "task_gid": task_gid,
                "task_name": task_name,
                "from_section": from_sec,
                "to_section": to_sec,
                "creator": creator.get("name", "系統") if creator else "系統",
            })

    # 同一任務只保留最後一筆
    seen = {}
    for move in moves:
        seen[move["task_gid"]] = move
    return list(seen.values())


# ===== 續約聯絡期（環境到期日在 3 個月內）=====

def get_renewal_tasks(expiry_field_gid, today_tw, product_field_gid=None):
    """取得環境到期日在今天到 3 個月後之間的未完成任務（排除加購服務）"""
    if not expiry_field_gid:
        print("   ⚠️ 找不到「環境到期日」欄位，跳過續約聯絡期")
        return []

    deadline = today_tw + timedelta(days=RENEWAL_MONTHS * 30)
    today_str = today_tw.strftime("%Y-%m-%d")
    deadline_str = deadline.strftime("%Y-%m-%d")

    # 取得專案所有未完成任務（含 custom fields）
    params = {
        "projects.any": ASANA_PROJECT_GID,
        "is_subtask": False,
        "completed": False,
        "opt_fields": "name,assignee.name,memberships.section.name,custom_fields.gid,custom_fields.date_value,custom_fields.name,custom_fields.enum_value,custom_fields.enum_value.name",
        "limit": 100,
    }
    tasks = asana_get(f"workspaces/{ASANA_WORKSPACE_GID}/tasks/search", params)

    result = []
    for task in tasks:
        expiry_date = None
        product_value = None
        for cf in task.get("custom_fields", []):
            if cf.get("gid") == expiry_field_gid:
                dv = cf.get("date_value") or {}
                expiry_date = dv.get("date") if isinstance(dv, dict) else None
            if product_field_gid and cf.get("gid") == product_field_gid:
                ev = cf.get("enum_value") or {}
                product_value = ev.get("name") if isinstance(ev, dict) else None
                print(f"   [debug] 任務「{task.get('name')}」購買商品={product_value!r}")

        if not expiry_date:
            continue

        # 篩選今天 ~ 3 個月後到期
        if not (today_str <= expiry_date <= deadline_str):
            continue

        # 排除加購服務
        if product_value in ADDON_SERVICE_VALUES:
            continue

        section = ""
        for m in task.get("memberships", []):
            sec = m.get("section", {})
            if sec:
                section = sec.get("name", "")
                break

        assignee = task.get("assignee", {})
        result.append({
            "gid": task["gid"],
            "name": task.get("name", "未命名"),
            "expiry_date": expiry_date,
            "section": section,
            "assignee": assignee.get("name", "未指派") if assignee else "未指派",
        })

    # 依到期日由近到遠排序
    result.sort(key=lambda t: t["expiry_date"])
    return result


# ===== 停滯任務 =====

def get_stale_tasks(stale_before_iso):
    """取得超過 30 天未異動且尚未完成的任務（只查白名單區段）"""
    # 取得白名單區段的 GID
    all_sections = asana_get(f"projects/{ASANA_PROJECT_GID}/sections", {"opt_fields": "name"})
    watch_gids = [s["gid"] for s in all_sections if s.get("name") in STALE_WATCH_SECTIONS]
    if not watch_gids:
        print("   ⚠️ 找不到任何白名單區段，跳過停滯任務")
        return []

    params = {
        "modified_at.before": stale_before_iso,
        "sections.any": ",".join(watch_gids),
        "is_subtask": "false",
        "completed": "false",
        "opt_fields": "name,modified_at,memberships.section.name,assignee.name",
        "limit": 100,
    }
    tasks = asana_get(f"workspaces/{ASANA_WORKSPACE_GID}/tasks/search", params)
    print(f"   API 回傳 {len(tasks)} 筆")
    result = []
    for task in tasks:
        section = ""
        for m in task.get("memberships", []):
            sec = m.get("section", {})
            if sec:
                section = sec.get("name", "")
                break
        assignee = task.get("assignee", {})
        result.append({
            "gid": task["gid"],
            "name": task.get("name", "未命名"),
            "modified_at": task.get("modified_at", ""),
            "section": section,
            "assignee": assignee.get("name", "未指派") if assignee else "未指派",
        })
    result.sort(key=lambda t: t.get("modified_at", ""))
    return result


# ===== 組合訊息 =====

def build_message(invoice_tasks, invoice_total, payment_tasks, payment_total, section_moves, renewal_tasks, stale_tasks, since_tw, until_tw):
    weekdays = ["一", "二", "三", "四", "五", "六", "日"]
    since_str = f"{since_tw.strftime('%Y/%m/%d')} ({weekdays[since_tw.weekday()]})"
    until_str = f"{until_tw.strftime('%Y/%m/%d')} ({weekdays[until_tw.weekday()]})"

    lines = []
    lines.append("📋 購案商機 — 每週更新摘要")
    lines.append(f"📅 {since_str} ~ {until_str} (台灣時間)")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 1. 本週開立發票
    lines.append("▶ 【本週開立發票金額】")
    lines.append("")
    if invoice_tasks:
        total_str = f"NT$ {invoice_total:,.0f}" if invoice_total > 0 else "（所有任務均未填寫金額）"
        lines.append(f"💰 合計：{total_str}")
        lines.append("")
        for task in invoice_tasks:
            amt = task["amount"]
            amt_str = f"NT$ {amt:,.0f}" if amt is not None else "（未填寫）"
            name_link = slack_link(task_url(task["task_gid"]), task["name"])
            lines.append(f"• {name_link} — {amt_str}")
    else:
        lines.append("本週無開立發票紀錄")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 2. 本週已收款
    lines.append("▶ 【本週已收款金額】")
    lines.append("")
    if payment_tasks:
        total_str = f"NT$ {payment_total:,.0f}" if payment_total > 0 else "（所有任務均未填寫金額）"
        lines.append(f"💵 合計：{total_str}")
        lines.append("")
        for task in payment_tasks:
            amt = task["amount"]
            amt_str = f"NT$ {amt:,.0f}" if amt is not None else "（未填寫）"
            name_link = slack_link(task_url(task["task_gid"]), task["name"])
            lines.append(f"• {name_link} — {amt_str}")
    else:
        lines.append("本週無已收款紀錄")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 4. 階段移動
    lines.append(f"▶ 【本週任務階段移動】（{len(section_moves)} 筆）")
    lines.append("")
    if section_moves:
        for move in section_moves:
            name_link = slack_link(task_url(move["task_gid"]), move["task_name"])
            lines.append(f"• {name_link}")
            lines.append(f"  `{move['from_section']}` → `{move['to_section']}`（{move['creator']}）")
            lines.append("")
    else:
        lines.append("本週無任務階段移動")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 3. 續約聯絡期
    lines.append(f"▶ 【🔔 續約聯絡期（{RENEWAL_MONTHS} 個月內到期）】（{len(renewal_tasks)} 筆）")
    lines.append("")
    if renewal_tasks:
        for task in renewal_tasks:
            name_link = slack_link(task_url(task["gid"]), task["name"])
            expiry = task["expiry_date"]
            section = task["section"] or "無區段"
            assignee = task["assignee"]
            lines.append(f"• {name_link}")
            lines.append(f"  到期日：{expiry}｜階段：{section}｜負責人：{assignee}")
            lines.append("")
    else:
        lines.append(f"目前無 {RENEWAL_MONTHS} 個月內到期的任務 👍")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 4. 停滯任務
    lines.append(f"▶ 【⚠️ 超過 {STALE_DAYS} 天未異動】（{len(stale_tasks)} 筆）")
    lines.append("")
    if stale_tasks:
        for task in stale_tasks:
            name_link = slack_link(task_url(task["gid"]), task["name"])
            modified = task["modified_at"][:10] if task["modified_at"] else "不明"
            section = task["section"] or "無區段"
            assignee = task["assignee"]
            lines.append(f"• {name_link}")
            lines.append(f"  最後異動：{modified}｜階段：{section}｜負責人：{assignee}")
            lines.append("")
    else:
        lines.append(f"目前無超過 {STALE_DAYS} 天未異動的任務 👍")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("此摘要由 Claude Agent 自動產生，資料來源為 Asana「購案商機」專案。")

    return "\n".join(lines)


def send_to_slack(message):
    resp = requests.post(SLACK_WEBHOOK_URL, json={"text": message})
    resp.raise_for_status()
    if resp.text != "ok":
        raise RuntimeError(f"Slack Webhook 錯誤: {resp.text}")
    print("✅ 訊息已發送到 Slack")


def main():
    now_tw = datetime.now(TW_TZ)
    today_tw = now_tw.date()

    until_tw = now_tw.replace(hour=0, minute=0, second=0, microsecond=0)
    since_tw = until_tw - timedelta(days=7)
    stale_before_tw = now_tw - timedelta(days=STALE_DAYS)

    since_iso = since_tw.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    until_iso = until_tw.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    stale_before_iso = stale_before_tw.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    print(f"📅 週報範圍: {since_tw.strftime('%Y/%m/%d')} ~ {until_tw.strftime('%Y/%m/%d')} (台灣時間)")

    # 查找環境到期日欄位 GID
    print("\n🔍 查找「環境到期日」欄位...")
    expiry_field_gid = get_expiry_field_gid()
    print(f"   GID: {expiry_field_gid or '找不到'}")

    # 查找購買商品欄位 GID
    print("\n🔍 查找「購買商品」欄位...")
    product_field_gid = get_product_field_gid()
    print(f"   GID: {product_field_gid or '找不到'}")

    # 1. 本週發票
    print("\n🔍 搜尋本週開立發票...")
    invoice_tasks, invoice_total = get_weekly_invoice_tasks(since_iso, until_iso)
    print(f"   找到 {len(invoice_tasks)} 筆，合計 NT$ {invoice_total:,.0f}")

    # 2. 本週已收款
    print("\n🔍 搜尋本週已收款...")
    payment_tasks, payment_total = get_weekly_payment_tasks(since_iso, until_iso)
    print(f"   找到 {len(payment_tasks)} 筆，合計 NT$ {payment_total:,.0f}")

    # 3. 階段移動
    print("\n🔍 搜尋本週階段移動...")
    section_moves = get_section_moves(since_iso, until_iso)
    print(f"   找到 {len(section_moves)} 筆")

    # 4. 續約聯絡期
    print(f"\n🔍 搜尋 {RENEWAL_MONTHS} 個月內到期的任務...")
    renewal_tasks = get_renewal_tasks(expiry_field_gid, today_tw, product_field_gid)
    print(f"   找到 {len(renewal_tasks)} 筆")

    # 4. 停滯任務
    print(f"\n🔍 搜尋超過 {STALE_DAYS} 天未異動的任務...")
    stale_tasks = get_stale_tasks(stale_before_iso)
    print(f"   找到 {len(stale_tasks)} 筆")

    # 組合並發送
    message = build_message(invoice_tasks, invoice_total, payment_tasks, payment_total, section_moves, renewal_tasks, stale_tasks, since_tw, until_tw)
    print("\n📤 發送到 Slack...")
    send_to_slack(message)
    print("✅ 完成！")


if __name__ == "__main__":
    main()
