#!/usr/bin/env python3
"""Biletinial mekan sayfasını takip eder ve şu durumlarda haber verir:

  * mekana yeni bir oyun eklendiğinde,
  * mevcut bir oyuna yeni seans (tarih) eklendiğinde,
  * tükenmiş bir seansta tekrar bilet açıldığında (en az MIN_TICKETS bilet kalmışsa).

Sadece standart kütüphane kullanır. Bildirim kanalları ortam değişkenleriyle açılır:
  NTFY_TOPIC                            -> ntfy.sh push bildirimi
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID -> Telegram mesajı
  GITHUB_TOKEN + GITHUB_REPOSITORY      -> repoda issue açar (GitHub e-posta ile haber verir)
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
# Kaç kişilik bilet arıyoruz? Kalan bilet bundan azsa "bilet açıldı" bildirimi gönderilmez.
MIN_TICKETS = int(os.environ.get("MIN_TICKETS", "2"))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
VENUE_PATH = urllib.parse.urlparse(VENUE_URL).path.split("/", 2)[-1]  # 'mekan/muammer-karaca-tiyatrosu'


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


def text_of(fragment):
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def parse_venue(page):
    """Mekan sayfasındaki oyunları {slug: {...}} olarak döndürür."""
    events = {}
    found_structure = False

    # 1) Görünen oyun kartları: sayfa linki mekan filtresiyle (cinemaBranchId) geliyor
    if 'id="placeEventsList"' in page:
        found_structure = True
    for href, name in re.findall(
        r'<h3><a href="(/[a-z]{2}-[a-z]{2}/[^"]+)"[^>]*>(.*?)</a></h3>', page, re.S
    ):
        href = html.unescape(href)
        events[slug_of(href)] = {
            "name": text_of(name),
            "url": urllib.parse.urljoin(VENUE_URL, href.split("?")[0]),
            "page": urllib.parse.urljoin(VENUE_URL, href),
        }

    # 2) JSON-LD (kartlar eksik kalırsa diye)
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
            if url and slug_of(url) not in events:
                events[slug_of(url)] = {
                    "name": ev.get("name", "").strip(),
                    "url": url.split("?")[0],
                    "page": url,
                }

    if not found_structure:
        raise RuntimeError("Mekan sayfasının yapısı tanınmadı (site değişmiş ya da engellenmiş olabilir)")
    return events


def parse_sessions(page):
    """Oyun sayfasındaki, bu mekana ait seansları {seans_id: {...}} olarak döndürür."""
    if "ed-biletler" not in page and "seanceSelect" not in page:
        raise RuntimeError("Oyun sayfasının yapısı tanınmadı")

    sessions = {}
    blocks = re.split(r'<div class="ed-biletler__sehir__gun"', page)[1:]
    for block in blocks:
        if f"/{VENUE_PATH}\"" not in block:
            continue  # başka bir mekandaki seans
        link = re.search(r'class="seance-share"[^>]*data-url="([^"]+)"', block)
        button = re.search(r'<button[^>]*class="seanceSelect[^"]*"[^>]*>', block)
        if not link:
            continue
        sid = link.group(1).rstrip("/").rsplit("/", 1)[-1]
        label = re.search(r'itemprop="startDate"[^>]*>([^<]+)<', block)
        remaining = re.search(r"Son\s+(\d+)\s+Bilet", block)
        remaining = int(remaining.group(1)) if remaining else None
        on_sale = bool(button) and "disabled" not in button.group(0) and "tukendi" not in button.group(0)
        sessions[sid] = {
            "label": html.unescape(label.group(1)).strip() if label else "",
            "link": link.group(1),
            "remaining": remaining,
            # Biz kaç kişilik arıyorsak o kadar bilet var mı?
            "open": on_sale and (remaining is None or remaining >= MIN_TICKETS),
        }
    return sessions


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
        # Kalan bilet sayısı her dakika değiştiği için kaydedilmiyor (gereksiz commit olmasın)
        "events": {
            slug: {
                "name": ev["name"],
                "url": ev["url"],
                "sessions": {
                    sid: {"label": s["label"], "open": s["open"]}
                    for sid, s in sorted(ev["sessions"].items())
                },
            }
            for slug, ev in sorted(events.items())
        },
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


def session_line(s):
    if s["open"]:
        status = "✅ BİLET VAR"
        if s["remaining"] is not None:
            status += f" (son {s['remaining']} bilet)"
    elif s.get("remaining"):
        status = f"⚠️ sadece {s['remaining']} bilet kaldı"
    else:
        status = "❌ tükendi"
    return f"• {s['label']}: {status}\n  {s['link']}"


def find_changes(old_events, events):
    """Bildirim gerektiren değişiklikleri (başlık, mesaj) listesi olarak döndürür."""
    changes = []
    for slug, ev in events.items():
        old = old_events.get(slug)
        if old is None:
            lines = [f"🎭 {ev['name']}", ev["url"], ""]
            lines += [session_line(s) for s in ev["sessions"].values()] or ["(henüz seans görünmüyor)"]
            changes.append((f"Yeni oyun: {ev['name']}", "\n".join(lines)))
            continue
        if "sessions" not in old:
            continue  # eski formattaki durum dosyası: bu turu sadece kayıt için kullan
        for sid, s in ev["sessions"].items():
            old_s = old["sessions"].get(sid)
            if old_s is None:
                changes.append((f"Yeni seans: {ev['name']}", session_line(s)))
            elif s["open"] and not old_s["open"]:
                changes.append((f"Bilet açıldı: {ev['name']}", session_line(s)))
    return changes


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
            "Priority": "urgent",
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

    state = load_state()
    old_events = (state or {}).get("events", {})

    events = parse_venue(http(VENUE_URL))
    for slug, ev in events.items():
        try:
            ev["sessions"] = parse_sessions(http(ev["page"]))
        except Exception as e:
            # Sayfa bu tur açılmadıysa eski bilgiyi koru, yanlış "bilet açıldı" bildirimi gitmesin
            print(f"{ev['name']} sayfası okunamadı: {e}", file=sys.stderr)
            old = old_events.get(slug, {}).get("sessions", {})
            ev["sessions"] = {sid: {**s, "link": "", "remaining": None} for sid, s in old.items()}
        print(f"{ev['name']}:")
        for s in ev["sessions"].values():
            print("  " + session_line(s).replace("\n", "\n  "))

    if state is None:
        print("İlk çalıştırma: mevcut durum kaydedildi, bildirim gönderilmedi.")
    else:
        changes = find_changes(old_events, events)
        if not changes:
            print("Değişiklik yok.")
        elif len(changes) == 1:
            notify(*changes[0])
        else:
            title = "Muammer Karaca: " + ", ".join(t for t, _ in changes)
            notify(title[:200], "\n\n".join(f"{t}\n{b}" for t, b in changes))
        for title, _ in changes:
            print("Bildirim:", title)

    save_state(events)


if __name__ == "__main__":
    main()
