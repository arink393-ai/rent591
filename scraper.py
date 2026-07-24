# -*- coding: utf-8 -*-
"""
591 租屋每日追蹤器
- 每天由 GitHub Actions 於台灣時間 12:00 觸發
- 依 config.json 的搜尋條件抓取物件，過濾後比對前一天資料
- 標記「新上架 / 降價」，產出 docs/index.html 儀表板 + data/latest.json

注意：
1. 591 有反爬機制，會不定期改版。若某天抓不到資料，先看 debug 輸出，
   多半是 API 網址參數或 headers/cookie 要調整。
2. 請維持低頻（一天一次）並保留 REQUEST_DELAY，這是對站方的基本禮貌，也避免被擋。
3. 本工具僅供個人找房整理使用。
"""

import json
import re
import time
import html
import os
import sys
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs

import requests

try:
    from notify_email import send_email
except Exception:
    send_email = None

# ---------- 基本設定 ----------
TW = timezone(timedelta(hours=8))
NOW = datetime.now(TW)
ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
PREV_PATH = os.path.join(ROOT, "data", "state.json")     # 長期記憶：每個物件的價格歷史
LATEST_PATH = os.path.join(ROOT, "data", "latest.json")  # 今日結果快照
HTML_PATH = os.path.join(ROOT, "docs", "index.html")

LIST_API = "https://bff-house.591.com.tw/v3/web/rent/list"  # 591 於 2026 改版後的新 API
HOME_URL = "https://rent.591.com.tw"
REQUEST_DELAY = 3.0      # 每次 API 請求間隔秒數（請勿調太低）
PAGE_SIZE = 30
MAX_PAGES_PER_QUERY = 6  # 每個搜尋條件最多翻幾頁，避免過度請求

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def new_session(region):
    """建立打新版 bff-house API 用的 session（2026 改版後不再需要 CSRF token）。"""
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": HOME_URL + "/",
        "device": "pc",  # 新 API 認這個 header，缺了會回 419
    })
    s.cookies.set("urlJumpIp", str(region), domain=".591.com.tw", path="/")
    return s


def parse_query_from_url(url):
    """從使用者貼進 config 的 591 搜尋結果網址解析出 API 參數。
    這樣妳只要在 591 網頁上設好篩選，複製網址即可，不必記參數代碼。"""
    q = parse_qs(urlparse(url).query)
    params = {k: v[0] for k, v in q.items()}
    params.setdefault("region", "1")
    return params


def fetch_listings(query):
    """抓單一搜尋條件的所有物件（含翻頁）。"""
    region = query.get("region", "1")
    s = new_session(region)
    results = []
    first_row = 0
    for _ in range(MAX_PAGES_PER_QUERY):
        params = dict(query)
        params["firstRow"] = str(first_row)
        try:
            r = s.get(LIST_API, params=params, timeout=20)
            data = r.json()
        except Exception as e:
            print(f"  [警告] 解析失敗 firstRow={first_row}: {e}", file=sys.stderr)
            print(f"  回應前 300 字：{r.text[:300] if 'r' in dir() else 'N/A'}",
                  file=sys.stderr)
            break

        # 新版 bff-house API 結構為 {'data': {'items': [...], 'total': 123}}
        block = data.get("data", {}) if isinstance(data, dict) else {}
        items = block.get("items") or block.get("topData") or []
        if not items:
            break
        results.extend(items)

        total = int(str(block.get("total", "0")).replace(",", "") or 0)
        first_row += PAGE_SIZE
        if first_row >= total:
            break
        time.sleep(REQUEST_DELAY)
    return results


# ---------- 欄位正規化 ----------
def g(item, *keys, default=""):
    for k in keys:
        if isinstance(item, dict) and item.get(k) not in (None, ""):
            return item[k]
    return default


def normalize(item):
    hid = str(g(item, "id", "post_id", "houseid", default=""))
    price_raw = str(g(item, "price", default="0")).replace(",", "")
    price = int(re.sub(r"[^\d]", "", price_raw) or 0)
    photo_list = g(item, "photoList", default=[]) or []
    return {
        "id": hid,
        "title": html.unescape(str(g(item, "title", default="(無標題)"))),
        "price": price,
        "price_unit": g(item, "price_unit", "unit", default="元/月"),
        "rooms": g(item, "layoutStr", "room", "layout", default=""),
        "area": g(item, "area_name", "area", default=""),
        "floor": g(item, "floor_name", "floor_str", "floor", default=""),
        "kind": g(item, "kind_name", "kind_str", default=""),
        "address": g(item, "address", "location", "section_name", default=""),
        "community": g(item, "community_name", "community", default=""),
        "role": g(item, "role_name", "kind", default=""),   # 屋主 / 仲介
        "tags": [t.get("value", t) if isinstance(t, dict) else t
                 for t in (g(item, "tags", "tag", default=[]) or [])],
        "photo": (photo_list[0] if photo_list else g(item, "cover", "photoSrc", default="")),
        "url": g(item, "url", default=(f"https://rent.591.com.tw/{hid}" if hid else "")),
        "region_name": g(item, "regionName", default=""),
        "section_name": g(item, "sectionName", default=""),
    }


def passes_hard_filter(row, cfg):
    """程式端能過濾的硬條件。"""
    if row["price"] <= 0:
        return False
    if row["price"] > cfg["max_price"]:
        return False
    # 房數：抓標題/rooms 欄位裡的「N房」
    room_match = re.search(r"(\d+)房", str(row["rooms"]) + str(row["title"]))
    if room_match:
        n = int(room_match.group(1))
        if not (cfg["min_rooms"] <= n <= cfg["max_rooms"]):
            return False
    if cfg.get("owner_only"):
        if "屋主" not in str(row["role"]):
            return False
    return True


def annotate_manual_checks(row, cfg):
    """591 無法篩、需看屋時親自問房東的條件，逐項提示。"""
    checks = []
    text = (row["title"] + " ".join(str(t) for t in row["tags"])).lower()
    checks.append(("可養3隻貓＋簽寵物條款",
                   "寵" in row["title"] + "".join(map(str, row["tags"]))))
    checks.append(("台電台水/水費含租",
                   any(k in text for k in ["台電", "台水", "水費含", "含水電"])))
    checks.append(("管理費是否含於租金", "管理費" in text or "含管" in text))
    checks.append(("有機車位/好停車",
                   any(k in text for k in ["車位", "機車", "停車"])))
    checks.append(("有管理員/代收包裹",
                   any(k in text for k in ["管理員", "代收", "門禁"])))
    return checks


# ---------- 主流程 ----------
def load_state():
    if os.path.exists(PREV_PATH):
        with open(PREV_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def main():
    cfg = load_config()
    state = load_state()  # {id: {"price": int, "first_seen": iso, "title": str}}
    seen_today = {}
    all_rows = []

    for query_cfg in cfg["searches"]:
        name = query_cfg.get("name", "查詢")
        url = query_cfg["url"]
        print(f"▶ 抓取：{name}")
        query = parse_query_from_url(url)
        raw = fetch_listings(query)
        print(f"  取得 {len(raw)} 筆原始資料")
        for item in raw:
            row = normalize(item)
            if not row["id"]:
                continue
            if not passes_hard_filter(row, cfg):
                continue
            row["group"] = name
            row["manual_checks"] = annotate_manual_checks(row, cfg)

            prev = state.get(row["id"])
            if prev is None:
                row["status"] = "new"
                row["price_delta"] = 0
            elif row["price"] < int(prev.get("price", row["price"])):
                row["status"] = "price_drop"
                row["price_delta"] = row["price"] - int(prev["price"])
            elif row["price"] > int(prev.get("price", row["price"])):
                row["status"] = "price_up"
                row["price_delta"] = row["price"] - int(prev["price"])
            else:
                row["status"] = "same"
                row["price_delta"] = 0

            seen_today[row["id"]] = {
                "price": row["price"],
                "title": row["title"],
                "first_seen": (prev or {}).get("first_seen", NOW.isoformat()),
                "last_seen": NOW.isoformat(),
            }
            all_rows.append(row)
        time.sleep(REQUEST_DELAY)

    # 去重（同一物件可能跨行政區重複）
    dedup = {}
    for r in all_rows:
        dedup[r["id"]] = r
    all_rows = list(dedup.values())

    # 排序：新上架 > 降價 > 其他；同組內價格低者優先
    order = {"new": 0, "price_drop": 1, "price_up": 2, "same": 3}
    all_rows.sort(key=lambda r: (order.get(r["status"], 9), r["price"]))

    # 更新長期記憶（保留今天看到的；舊的自然淘汰）
    with open(PREV_PATH, "w", encoding="utf-8") as f:
        json.dump(seen_today, f, ensure_ascii=False, indent=2)
    with open(LATEST_PATH, "w", encoding="utf-8") as f:
        json.dump({"updated": NOW.isoformat(), "rows": all_rows},
                  f, ensure_ascii=False, indent=2)

    render_html(all_rows, cfg)

    new_count = sum(1 for r in all_rows if r["status"] == "new")
    drop_count = sum(1 for r in all_rows if r["status"] == "price_drop")
    print(f"\n✅ 完成：共 {len(all_rows)} 筆符合，"
          f"新上架 {new_count}、降價 {drop_count}")

    # 寄 email 通知（未設定帳密則自動略過）
    if send_email:
        try:
            dash = cfg.get("dashboard_url", "")
            send_email(all_rows, cfg, NOW.strftime("%Y-%m-%d %H:%M"), dash)
        except Exception as e:
            print(f"⚠️  寄信失敗（不影響資料更新）：{e}", file=sys.stderr)


# ---------- 儀表板 ----------
def render_html(rows, cfg):
    new_rows = [r for r in rows if r["status"] == "new"]
    drop_rows = [r for r in rows if r["status"] == "price_drop"]
    updated = NOW.strftime("%Y-%m-%d %H:%M")

    def badge(r):
        if r["status"] == "new":
            return '<span class="b b-new">🐾 新上架</span>'
        if r["status"] == "price_drop":
            return f'<span class="b b-drop">▼ 降價 {abs(r["price_delta"]):,}</span>'
        if r["status"] == "price_up":
            return f'<span class="b b-up">▲ 漲價 {abs(r["price_delta"]):,}</span>'
        return ""

    def card(r):
        checks = "".join(
            f'<li class="{"ok" if ok else "no"}">'
            f'{"✓" if ok else "？"} {html.escape(label)}</li>'
            for label, ok in r["manual_checks"])
        tags = "".join(f'<span class="tag">{html.escape(str(t))}</span>'
                       for t in r["tags"][:6])
        return f"""
        <article class="card {r['status']}">
          <div class="card-head">
            <div class="price">{r['price']:,}<small>{html.escape(r['price_unit'])}</small></div>
            {badge(r)}
          </div>
          <a class="title" href="{html.escape(r['url'])}" target="_blank" rel="noopener">
            {html.escape(r['title'])}</a>
          <div class="meta">{html.escape(str(r['kind']))} ·
            {html.escape(str(r['rooms']))} · {html.escape(str(r['area']))} ·
            {html.escape(str(r['floor']))}</div>
          <div class="meta addr">{html.escape(str(r['region_name']))}
            {html.escape(str(r['section_name']))} {html.escape(str(r['address']))}</div>
          <div class="role">{html.escape(str(r['role']))}</div>
          <div class="tags">{tags}</div>
          <ul class="checks">{checks}</ul>
        </article>"""

    body = "".join(card(r) for r in rows) or \
        '<p class="empty">今天沒有符合條件的物件，或 591 參數需調整。</p>'

    doc = f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🐱 租屋追蹤器 · {updated}</title>
<style>
  :root {{ --paper:#f4ecd8; --ink:#4a3f35; --line:#d8c9a8;
           --new:#e8a33d; --drop:#3d8b6a; --up:#c05a4a; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:-apple-system,"PingFang TC","Noto Sans TC",sans-serif;
          background:var(--paper); color:var(--ink);
          background-image:radial-gradient(circle at 20% 30%, #0000000a 0, transparent 40%); }}
  header {{ padding:20px 16px 12px; border-bottom:2px dashed var(--line); }}
  h1 {{ margin:0; font-size:20px; }}
  .sub {{ font-size:13px; opacity:.7; margin-top:4px; }}
  .stats {{ display:flex; gap:8px; margin-top:10px; flex-wrap:wrap; }}
  .stat {{ background:#fff8; border:1px solid var(--line); border-radius:10px;
           padding:6px 12px; font-size:13px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr));
           gap:12px; padding:16px; }}
  .card {{ background:#fffdf7; border:1px solid var(--line); border-radius:14px;
           padding:14px; box-shadow:0 2px 6px #0000000d; }}
  .card.new {{ border-color:var(--new); box-shadow:0 2px 10px #e8a33d33; }}
  .card.price_drop {{ border-color:var(--drop); }}
  .card-head {{ display:flex; justify-content:space-between; align-items:center; }}
  .price {{ font-size:22px; font-weight:800; color:#8a5a2b; }}
  .price small {{ font-size:12px; font-weight:400; opacity:.7; margin-left:2px; }}
  .b {{ font-size:12px; padding:3px 8px; border-radius:20px; color:#fff; }}
  .b-new {{ background:var(--new); }} .b-drop {{ background:var(--drop); }}
  .b-up {{ background:var(--up); }}
  .title {{ display:block; font-weight:700; margin:8px 0 4px; color:var(--ink);
            text-decoration:none; line-height:1.4; }}
  .title:hover {{ text-decoration:underline; }}
  .meta {{ font-size:13px; opacity:.8; }} .addr {{ margin-top:2px; }}
  .role {{ display:inline-block; margin-top:6px; font-size:12px;
           background:#eadfc4; padding:2px 8px; border-radius:6px; }}
  .tags {{ margin:8px 0 6px; display:flex; gap:4px; flex-wrap:wrap; }}
  .tag {{ font-size:11px; background:#efe6cf; padding:2px 6px; border-radius:4px; }}
  .checks {{ list-style:none; padding:0; margin:6px 0 0; font-size:12px; }}
  .checks li {{ padding:1px 0; }} .checks .ok {{ color:var(--drop); }}
  .checks .no {{ color:#a08f70; }}
  .empty {{ padding:40px; text-align:center; opacity:.6; }}
  footer {{ padding:16px; font-size:11px; opacity:.5; text-align:center; }}
</style></head><body>
<header>
  <h1>🐱 幸せ租屋追蹤器</h1>
  <div class="sub">更新於 {updated}（每天中午自動更新）· 打勾為系統推測，實際仍需看屋時向房東確認</div>
  <div class="stats">
    <span class="stat">符合 {len(rows)} 筆</span>
    <span class="stat">🐾 新上架 {len(new_rows)}</span>
    <span class="stat">▼ 降價 {len(drop_rows)}</span>
    <span class="stat">上限 {cfg['max_price']:,} 元</span>
  </div>
</header>
<div class="grid">{body}</div>
<footer>個人找房用途 · 資料來源 591 · 條款細節（三貓／寵物附約／獨立電水）以房東現場說明為準</footer>
</body></html>"""

    os.makedirs(os.path.dirname(HTML_PATH), exist_ok=True)
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(doc)


if __name__ == "__main__":
    main()
