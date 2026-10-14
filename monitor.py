#!/usr/bin/env python3
"""Biletinial mekan sayfasını kontrol eder, yeni bir oyun listelendiğinde haber verir.

Sadece standart kütüphane kullanır. Bildirim kanalları ortam değişkenleriyle açılır:
  NTFY_TOPIC                          -> ntfy.sh push bildirimi
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID -> Telegram mesajı
  GITHUB_TOKEN + GITHUB_REPOSITORY    -> repoda issue açar (GitHub e-posta ile haber verir)
"""

import base64
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

VENUE_URL = os.environ.get(
    "VENUE_URL", "https://biletinial.com/tr-tr/mekan/muammer-karaca-tiyatrosu"
)
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def http(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    req.add_header("User-Agent", USER_AGENT)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def slug_of(url):
    """'/tr-tr/tiyatro/oyun-adi?cinemaBranchId=1' -> 'tiyatro/oyun-adi'"""
    path = urllib.parse.urlparse(url).path.strip("/")
    parts = path.split("/")
    if len(parts) >= 2 and re.fullmatch(r"[a-z]{2}-[a-z]{2}", parts[0]):
        parts = parts[1:]
    return "/".join(parts)


def parse_events(page):
    """Sayfadaki oyunları {slug: {...}} olarak döndürür. Sayfa beklenen yapıda değilse hata verir."""
    events = {}
    found_structure = False

    # 1) JSON-LD: isim, URL ve en yakın tarih
    for block in re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', page, re.S
    ):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if data.get("@type") != "PerformingArtsTheater":
            continue
        found_structure = True
        for ev in data.get("event") or []:
            url = ev.get("url")
            if not url:
                continue
            events[slug_of(url)] = {
                "name": ev.get("name", "").strip(),
                "url": url.split("?")[0],
                "date": ev.get("startDate", ""),
            }

    # 2) Görünen oyun kartları (JSON-LD eksik kalırsa diye)
    if 'id="placeEventsList"' in page:
        found_structure = True
    for href, name in re.findall(
        r'<h3><a href="(/[a-z]{2}-[a-z]{2}/[^"]+)"[^>]*>(.*?)</a></h3>', page, re.S
    ):
        slug = slug_of(href)
        if slug not in events:
            events[slug] = {
                "name": html.unescape(re.sub(r"<[^>]+>", "", name)).strip(),
                "url": urllib.parse.urljoin(VENUE_URL, href.split("?")[0]),
                "date": "",
            }

    if not found_structure:
        raise RuntimeError("Sayfa yapısı tanınmadı (site değişmiş ya da engellenmiş olabilir)")
    return events


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save_state(events):
    state = {
        "venue": VENUE_URL,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "events": dict(sorted(events.items())),
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


def format_date(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y")
    except ValueError:
        return ""


def build_message(new_events):
    lines = []
    for ev in new_events:
        date = format_date(ev["date"])
        lines.append(f"🎭 {ev['name']}" + (f" (ilk tarih: {date})" if date else ""))
        lines.append(ev["url"])
        lines.append("")
    lines.append(f"Mekan: {VENUE_URL}")
    return "\n".join(lines)


def notify_ntfy(title, body):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    http(
        f"{server}/{urllib.parse.quote(topic)}",
        data=body.encode("utf-8"),
        headers={
            # HTTP başlıkları latin-1 olmalı; Türkçe başlık için RFC 2047 kodlaması
            "Title": "=?UTF-8?B?" + base64.b64encode(title.encode()).decode() + "?=",
            "Priority": "high",
            "Tags": "ticket",
            "Click": VENUE_URL,
        },
        method="POST",
    )
    print("ntfy bildirimi gönderildi")


def notify_telegram(title, body):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return
    payload = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": f"{title}\n\n{body}", "disable_web_page_preview": "false"}
    ).encode()
    http(f"https://api.telegram.org/bot{token}/sendMessage", data=payload)
    print("Telegram bildirimi gönderildi")


def notify_github_issue(title, body):
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not (token and repo) or os.environ.get("GITHUB_ISSUE_NOTIFY", "true") != "true":
        return
    http(
        f"https://api.github.com/repos/{repo}/issues",
        data=json.dumps({"title": title, "body": body}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    print("GitHub issue açıldı")


def notify(title, body):
    errors = []
    for fn in (notify_ntfy, notify_telegram, notify_github_issue):
        try:
            fn(title, body)
        except Exception as e:  # bir kanal çökerse diğerleri yine denensin
            errors.append(f"{fn.__name__}: {e}")
    for err in errors:
        print("Bildirim hatası:", err, file=sys.stderr)


def main():
    if "--test-notify" in sys.argv:
        notify("Bilet takip testi", "Bildirimler çalışıyor ✅\n\n" + VENUE_URL)
        return

    events = parse_events(http(VENUE_URL))
    print(f"{len(events)} oyun bulundu: " + ", ".join(e["name"] for e in events.values()))

    state = load_state()
    if state is None:
        print("İlk çalıştırma: mevcut liste kaydedildi, bildirim gönderilmedi.")
        save_state(events)
        return

    new_slugs = [s for s in events if s not in state.get("events", {})]
    if new_slugs:
        new_events = [events[s] for s in new_slugs]
        names = ", ".join(e["name"] for e in new_events)
        title = f"Muammer Karaca'da yeni oyun: {names}"
        print(title)
        notify(title, build_message(new_events))
    else:
        print("Yeni oyun yok.")

    save_state(events)


if __name__ == "__main__":
    main()
