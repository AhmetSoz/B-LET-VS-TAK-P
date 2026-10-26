#!/usr/bin/env python3
"""Burs botu: burslar.json'daki tam burslu yüksek lisans seçeneklerini e-posta ile gönderir.

E-postada önce yaklaşan son başvuru tarihleri, sonra sıralı kısa liste yer alır.
Gmail SMTP kullanır; gereken ortam değişkenleri:
  GMAIL_USER          gönderen Gmail adresi
  GMAIL_APP_PASSWORD  Google hesabından alınan 16 haneli uygulama şifresi
  MAIL_TO             alıcılar, virgülle ayrılmış

  python burs/mail.py            -> e-postayı gönderir
  python burs/mail.py --preview  -> göndermez, burs/onizleme.html dosyasına yazar
"""

import html
import json
import os
import smtplib
import ssl
import sys
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "burslar.json"
UPCOMING_DAYS = int(os.environ.get("UPCOMING_DAYS") or "60")
TODAY = datetime.now(timezone(timedelta(hours=3))).date()

IELTS_BADGE = {
    "uygun": ("#1a7f37", "IELTS 6.0 yeterli"),
    "sinirda": ("#9a6700", "IELTS sınırda / alt puan şartı var"),
    "yuksek": ("#cf222e", "IELTS 6.0 yetmiyor"),
    "gerekmiyor": ("#1a7f37", "IELTS şart değil"),
}


def esc(x):
    return html.escape(str(x or ""))


def parse_date(s):
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def deadline_text(item):
    d = parse_date(item.get("deadline"))
    note = item.get("deadline_note") or ""
    if not d:
        return note or "Tarih henüz açıklanmadı"
    days = (d - TODAY).days
    when = d.strftime("%d.%m.%Y")
    if days < 0:
        rel = "süresi geçti"
    elif days == 0:
        rel = "BUGÜN son gün"
    else:
        rel = f"{days} gün kaldı"
    return f"{when} ({rel})" + (f" — {note}" if note else "")


def card(item):
    color, label = IELTS_BADGE.get(item.get("ielts_fit"), ("#57606a", "IELTS: kontrol et"))
    rows = [
        ("Ülke / Üniversite", item.get("where")),
        ("Alan", item.get("field")),
        ("Neleri karşılıyor", item.get("coverage")),
        ("İngilizce şartı", item.get("ielts")),
        ("Türk vatandaşları", item.get("turkey")),
        ("Son başvuru", deadline_text(item)),
        ("Not", item.get("notes")),
    ]
    body = "".join(
        f'<tr><td style="padding:4px 8px 4px 0;color:#57606a;vertical-align:top;width:32%">{esc(k)}</td>'
        f'<td style="padding:4px 8px">{esc(v)}</td></tr>'
        for k, v in rows if v
    )
    return f"""
<div style="border:1px solid #d0d7de;border-radius:8px;padding:12px 14px;margin:0 0 14px">
  <div style="font-size:16px;font-weight:600">{esc(item.get('rank'))}. {esc(item['name'])}</div>
  <div style="margin:6px 0 8px"><span style="background:{color};color:#fff;border-radius:10px;padding:2px 8px;font-size:12px">{esc(label)}</span></div>
  <table style="border-collapse:collapse;font-size:14px;width:100%;table-layout:fixed">{body}</table>
  <div style="margin-top:8px"><a href="{esc(item.get('link'))}">Resmî sayfa →</a></div>
</div>"""


def build(data, extra_html="", extra_text=""):
    """Haftalık tam özet. extra_*: bot'un eklediği değişiklik / yeni burs bölümleri."""
    items = sorted(data["items"], key=lambda i: i.get("rank", 999))
    upcoming = sorted(
        (i for i in items if (d := parse_date(i.get("deadline"))) and 0 <= (d - TODAY).days <= UPCOMING_DAYS),
        key=lambda i: i["deadline"],
    )
    up_html = "".join(
        f'<li><b>{esc(parse_date(i["deadline"]).strftime("%d.%m.%Y"))}</b> — {esc(i["name"])} '
        f'({(parse_date(i["deadline"]) - TODAY).days} gün kaldı)</li>'
        for i in upcoming
    ) or f"<li>Önümüzdeki {UPCOMING_DAYS} günde son başvuru tarihi olan bir burs yok.</li>"
    intro = esc(data.get("intro", ""))
    report = data.get("report_url")
    html_body = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#1f2328;max-width:720px;margin:auto;padding:12px;overflow-wrap:anywhere">
<h2 style="margin:0 0 6px">🎓 Tam burslu yüksek lisans seçenekleri</h2>
<div style="color:#57606a;font-size:13px;margin-bottom:12px">Sosyoloji · İnsan hakları · Sosyal bilimler — IELTS 6.0 · {TODAY.strftime('%d.%m.%Y')}</div>
<p>{intro}</p>
{extra_html}
<h3>⏰ Yaklaşan son başvuru tarihleri ({UPCOMING_DAYS} gün)</h3>
<ul>{up_html}</ul>
<h3>📋 Sıralı kısa liste</h3>
{''.join(card(i) for i in items)}
{f'<p><a href="{esc(report)}">Ayrıntılı araştırma raporu →</a></p>' if report else ''}
<p style="color:#57606a;font-size:12px">Bilgiler resmî kaynaklardan derlendi ama burs koşulları her yıl değişebilir; başvurmadan önce resmî sayfadan teyit edin.</p>
</body></html>"""
    text_body = extra_text + "Tam burslu yüksek lisans seçenekleri\n\n" + "\n\n".join(
        f"{i.get('rank')}. {i['name']}\n{i.get('where','')}\nİngilizce: {i.get('ielts','')}\n"
        f"Son başvuru: {deadline_text(i)}\n{i.get('link','')}"
        for i in items
    )
    subject = data.get("subject", "🎓 Burslu yüksek lisans listesi")
    if upcoming:
        subject += f" — {len(upcoming)} başvurunun süresi yaklaşıyor"
    return subject, text_body, html_body


def wrap(title, inner_html):
    """Kısa (günlük) e-postalar için aynı görünümde sayfa."""
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#1f2328;max-width:720px;margin:auto;padding:12px;overflow-wrap:anywhere">
<h2 style="margin:0 0 10px">{esc(title)}</h2>{inner_html}
<p style="color:#57606a;font-size:12px">Burs Botu · {TODAY.strftime('%d.%m.%Y')} · Bilgiler değişebilir; başvurmadan önce resmî sayfadan teyit edin.</p>
</body></html>"""


def send(subject, text_body, html_body):
    user = os.environ["GMAIL_USER"]
    password = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
    to = [a.strip() for a in os.environ["MAIL_TO"].split(",") if a.strip()]
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"Burs Botu <{user}>"
    msg["To"] = ", ".join(to)
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(user, password)
        s.send_message(msg)
    print(f"E-posta gönderildi: {len(to)} alıcı")


def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    subject, text_body, html_body = build(data)
    if "--preview" in sys.argv:
        out = HERE / "onizleme.html"
        out.write_text(html_body, encoding="utf-8")
        print(subject, "->", out)
        return
    send(subject, text_body, html_body)


if __name__ == "__main__":
    main()
