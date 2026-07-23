# -*- coding: utf-8 -*-
"""
Email 通知模組
- 讀取環境變數 GMAIL_USER / GMAIL_APP_PASSWORD / MAIL_TO（由 GitHub Secrets 注入）
- 只在有「新上架」或「降價」時寄信；沒新東西就安靜跳過（可用 ALWAYS_SEND=1 強制寄）
- 若未設定帳密，直接略過，不影響爬蟲主流程
"""

import os
import ssl
import smtplib
import html
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr


def _fmt_card(r):
    delta = ""
    if r["status"] == "price_drop":
        delta = f'（▼ 降 {abs(r["price_delta"]):,}）'
    checks = " · ".join(
        f'{"✓" if ok else "？"}{label}' for label, ok in r["manual_checks"])
    addr = f'{r.get("region_name","")}{r.get("section_name","")} {r.get("address","")}'.strip()
    return f"""
    <tr><td style="padding:12px 14px;border:1px solid #d8c9a8;border-radius:12px;
        background:#fffdf7;display:block;margin-bottom:10px;">
      <div style="font-size:20px;font-weight:800;color:#8a5a2b;">
        {r['price']:,}<span style="font-size:12px;font-weight:400;color:#999;">
        {html.escape(r['price_unit'])}</span>
        <span style="font-size:12px;color:#c05a4a;">{delta}</span></div>
      <a href="{html.escape(r['url'])}" style="color:#4a3f35;font-weight:700;
        text-decoration:none;font-size:15px;">{html.escape(r['title'])}</a>
      <div style="font-size:13px;color:#666;margin-top:3px;">
        {html.escape(str(r['kind']))} · {html.escape(str(r['rooms']))} ·
        {html.escape(str(r['area']))} · {html.escape(str(r['floor']))}</div>
      <div style="font-size:13px;color:#666;">{html.escape(addr)}</div>
      <div style="font-size:12px;color:#8a5a2b;margin-top:4px;">
        {html.escape(str(r['role']))}</div>
      <div style="font-size:12px;color:#a08f70;margin-top:4px;">
        看屋要問：{html.escape(checks)}</div>
    </td></tr>"""


def send_email(rows, cfg, updated_str, dashboard_url=""):
    user = os.environ.get("GMAIL_USER", "").strip()
    pwd = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    to_raw = os.environ.get("MAIL_TO", user).strip()
    always = os.environ.get("ALWAYS_SEND", "").strip() == "1"

    if not user or not pwd:
        print("ℹ️  未設定 GMAIL_USER / GMAIL_APP_PASSWORD，略過寄信。")
        return

    new_rows = [r for r in rows if r["status"] == "new"]
    drop_rows = [r for r in rows if r["status"] == "price_drop"]

    if not new_rows and not drop_rows and not always:
        print("ℹ️  今天沒有新上架或降價，不寄信。")
        return

    to_list = [a.strip() for a in to_raw.split(",") if a.strip()]
    subject = f"🐱 租屋快報 {updated_str}：新 {len(new_rows)}・降價 {len(drop_rows)}"

    sections = ""
    if new_rows:
        sections += ('<h3 style="color:#e8a33d;margin:18px 0 6px;">🐾 新上架 '
                     f'{len(new_rows)} 筆</h3><table style="width:100%;border-collapse:'
                     'separate;">' + "".join(_fmt_card(r) for r in new_rows) + "</table>")
    if drop_rows:
        sections += ('<h3 style="color:#3d8b6a;margin:18px 0 6px;">▼ 降價 '
                     f'{len(drop_rows)} 筆</h3><table style="width:100%;border-collapse:'
                     'separate;">' + "".join(_fmt_card(r) for r in drop_rows) + "</table>")
    if not sections:
        sections = ('<p style="color:#888;">今天沒有新上架或降價物件，'
                    '此為每日確認信。</p>')

    dash_line = (f'<p style="margin-top:16px;"><a href="{html.escape(dashboard_url)}" '
                 f'style="color:#8a5a2b;">👉 打開完整儀表板（含全部符合物件）</a></p>'
                 if dashboard_url else "")

    body = f"""<div style="font-family:-apple-system,'PingFang TC',sans-serif;
        max-width:640px;margin:auto;background:#f4ecd8;padding:20px;">
      <h2 style="margin:0 0 4px;color:#4a3f35;">🐱 幸せ租屋每日快報</h2>
      <div style="font-size:13px;color:#777;">更新 {html.escape(updated_str)} ·
        租金上限 {cfg['max_price']:,} 元 · {cfg['min_rooms']}–{cfg['max_rooms']} 房</div>
      {sections}
      {dash_line}
      <p style="font-size:11px;color:#aaa;margin-top:20px;border-top:1px dashed #d8c9a8;
        padding-top:10px;">「看屋要問」的✓為系統推測，三隻貓／寵物附約／獨立電水等
        細節仍須向房東當面確認並寫入合約。此信為個人找房用途自動發送。</p>
    </div>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr(("租屋追蹤器", user))
    msg["To"] = ", ".join(to_list)
    msg.attach(MIMEText(body, "html", "utf-8"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
        server.login(user, pwd)
        server.sendmail(user, to_list, msg.as_string())
    print(f"✅ 已寄出通知信給 {', '.join(to_list)}")
