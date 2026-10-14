#!/usr/bin/env python3
"""Tiyatro bilet takipçisi.

config.json'daki kaynakları (Biletinial mekan sayfaları, İBB Şehir Tiyatroları) takip eder
ve yetişkin oyunları için şu durumlarda haber verir:

  * yeni bir oyun eklendiğinde,
  * bir oyuna yeni seans (tarih) eklendiğinde,
  * bir seansta bilet satışa açıldığında (tükenmişken iade, "yakında" iken satış başlaması),
    en az MIN_TICKETS bilet kalmışsa.

izlenenler.txt'deki oyunlar (izlediklerimiz) atlanır. Telegram'dan bota
"/gittik <oyun adı>" yazılarak da bu listeye ekleme yapılabilir.

Sadece standart kütüphane kullanır. Bildirim kanalları ortam değişkenleriyle açılır:
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID -> Telegram mesajı
  NTFY_TOPIC                            -> ntfy.sh push bildirimi
  GITHUB_TOKEN + GITHUB_REPOSITORY      -> başka kanal yoksa repoda issue açar
"""

import base64
import hashlib
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

CONFIG_FILE = os.environ.get("CONFIG_FILE", "config.json")
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
WATCHED_FILE = os.environ.get("WATCHED_FILE", "izlenenler.txt")
# Kaç kişilik bilet arıyoruz? Kalan bilet bundan azsa "bilet açıldı" bildirimi gönderilmez.
MIN_TICKETS = int(os.environ.get("MIN_TICKETS") or "2")
# Aynı seans için "bilet açıldı" bildirimi en fazla bu sıklıkta (sepet iptalleriyle
# seans tükendi/açıldı arasında gidip gelebiliyor)
REOPEN_COOLDOWN = 60 * 60
# Bir kaynak bu kadar tur üst üste okunamazsa haber ver (5 dk x 12 = 1 saat)
FAIL_ALERT_AFTER = 12
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
ISTANBUL = timezone(timedelta(hours=3))

OPEN, FEW, SOON, SOLDOUT = "open", "few", "soon", "soldout"


# --------------------------------------------------------------------------- yardımcılar


def http(url, data=None, headers=None, method=None):
    if data is None:
        # Şehir Tiyatroları sayfaları CDN'de dakikalarca bayat kalabiliyor; her seferinde
        # farklı bir parametre ekleyerek güncel sayfayı alıyoruz.
        url += ("&" if "?" in url else "?") + f"_={int(time.time() * 1000)}"
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    req.add_header("User-Agent", USER_AGENT)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def text_of(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def normalize(name):
    """'KÖPEK KALBİ' ve 'köpek kalbi' aynı sayılsın diye isimleri sadeleştirir."""
    name = name.translate(str.maketrans("İIıÇçĞğÖöŞşÜüÂâÎîÛû", "iiiccggoossuuaaiiuu"))
    name = unicodedata.normalize("NFKD", name.lower())
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def is_watched(name, watched):
    n = normalize(name)
    return any(w == n or (len(w) >= 4 and w in n) for w in watched)


def acilis_status(acilis):
    """data-acilis='202610240710' -> geçmişse OPEN, değilse (SOON, '24.10 07:10')."""
    try:
        t = datetime.strptime(acilis, "%Y%m%d%H%M").replace(tzinfo=ISTANBUL)
    except ValueError:
        return SOON, ""
    if t <= datetime.now(ISTANBUL):
        return OPEN, ""
    return SOON, t.strftime("%d.%m %H:%M")


def session(label, stage, link, status, remaining=None, opens=""):
    if status == OPEN and remaining is not None and remaining < MIN_TICKETS:
        status = FEW
    return {"label": label, "stage": stage, "link": link, "status": status,
            "remaining": remaining, "opens": opens}


def fetch_sessions(plays, todo, fn):
    """Oyun sayfalarını paralel okur. Okunamayan oyunun "sessions" alanı boş kalır;
    main() o oyun için eski kaydı korur (yanlış bildirim gitmesin)."""
    def safe(slug):
        try:
            return fn(plays[slug]["page"])
        except Exception as e:
            print(f"   {plays[slug]['name']} okunamadı: {e}", file=sys.stderr)
            return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        for slug, sessions in zip(todo, pool.map(safe, todo)):
            if sessions is not None:
                plays[slug]["sessions"] = sessions
    if todo and not any("sessions" in plays[s] for s in todo):
        raise RuntimeError("Hiçbir oyun sayfası okunamadı")


# --------------------------------------------------------------------------- Biletinial


def bi_list_plays(url):
    page = http(url)
    if 'id="placeEventsList"' not in page:
        raise RuntimeError(f"Mekan sayfası tanınmadı: {url}")
    plays = {}
    for href, name in re.findall(
        r'<h3><a href="(/[a-z]{2}-[a-z]{2}/[^"]+)"[^>]*>(.*?)</a></h3>', page, re.S
    ):
        href = html.unescape(href)
        slug = urllib.parse.urlparse(href).path.rstrip("/").split("/", 2)[-1]
        plays[slug] = {"name": text_of(name), "page": urllib.parse.urljoin(url, href)}
    return plays


def bi_sessions(page_url, venue_path):
    page = http(page_url)
    if "<h1" not in page:
        raise RuntimeError("sayfa tanınmadı")
    sessions = {}
    for block in re.split(r'<div class="ed-biletler__sehir__gun"', page)[1:]:
        place = re.search(rf'<a href="{re.escape(venue_path)}" title="([^"]*)"', block)
        link = re.search(r'class="seance-share"[^>]*data-url="([^"]+)"', block)
        if not (place and link):
            continue  # başka mekandaki seans
        button = re.search(r'<button[^>]*class="seanceSelect[^"]*"[^>]*>', block)
        label = re.search(r'itemprop="startDate"[^>]*>([^<]+)<', block)
        remaining = re.search(r"Son\s+(\d+)\s+Bilet", block)
        remaining = int(remaining.group(1)) if remaining else None
        status, opens = SOLDOUT, ""
        if button:
            tag = button.group(0)
            acilis = re.search(r'data-acilis="(\d+)"', tag)
            if "tukendi" in tag or "disabled" in tag:
                status = SOLDOUT
            elif acilis or "yakinda" in tag:
                status, opens = acilis_status(acilis.group(1) if acilis else "")
            else:
                status = OPEN
        sid = link.group(1).rstrip("/").rsplit("/", 1)[-1]
        sessions[sid] = session(
            html.unescape(label.group(1)).strip() if label else "",
            html.unescape(place.group(1)).strip(),
            link.group(1), status, remaining, opens,
        )
    return sessions


def fetch_biletinial(src, watched):
    url = src["url"]
    venue_path = urllib.parse.urlparse(url).path
    plays = bi_list_plays(url)
    kids = bi_list_plays(url + ("&" if "?" in url else "?") + "isKids=true")
    plays = {s: p for s, p in plays.items() if s not in kids}
    todo = [s for s, p in plays.items() if not is_watched(p["name"], watched)]
    fetch_sessions(plays, todo, lambda page: bi_sessions(page, venue_path))
    return plays


# --------------------------------------------------------------------------- İBB Şehir Tiyatroları


def st_list_plays(base, category=None):
    plays = {}
    for page_no in range(1, 20):
        q = {"page": page_no}
        if category:
            q["category"] = category
        page = http(f"{base}/oyunlar?{urllib.parse.urlencode(q)}")
        found = re.findall(r'<a href="/?oyun/([^"]+)" title="([^"]*)"', page)
        if page_no == 1 and "Türe Göre Listele" not in page:
            raise RuntimeError("Şehir Tiyatroları oyun listesi tanınmadı")
        new = {s: html.unescape(n).strip() for s, n in found if s not in plays}
        if not new:
            break
        for slug, name in new.items():
            plays[slug] = {"name": name, "page": f"{base}/oyun/{slug}"}
    return plays


def st_sessions(page_url, base):
    page = http(page_url)
    if "<h1" not in page:
        raise RuntimeError("sayfa tanınmadı")
    sessions = {}
    for block in re.split(r'src="/lib/images/calendar\.svg"', page)[1:]:
        label = re.search(r"<time[^>]*>([^<]+)</time>", block)
        stage = re.search(r'<address.*?title="([^"]*)"', block, re.S)
        button = re.search(r'<a [^>]*class="button_filled[^"]*"[^>]*>([^<]*)</a>', block)
        if not (label and button):
            continue
        label = html.unescape(label.group(1)).strip()
        stage = html.unescape(stage.group(1)).strip() if stage else ""
        tag, text = button.group(0), button.group(1)
        href = re.search(r'href="(/koltuk-secimi/\d+)"', tag)
        acilis = re.search(r'data-acilis="(\d+)"', tag)
        if "Tükendi" in tag or "Tükendi" in text:
            status, opens = SOLDOUT, ""
        elif acilis:
            status, opens = acilis_status(acilis.group(1))
        elif href:
            status, opens = OPEN, ""
        else:
            status, opens = SOLDOUT, ""
        # Tükenmiş seansların linki/ID'si yok; seansı tarih + sahne ile tanıyoruz
        sid = hashlib.sha1(f"{label}|{stage}".encode()).hexdigest()[:12]
        link = base + href.group(1) if href and status == OPEN else page_url + "#gosterimler"
        sessions[sid] = session(label, stage, link, status, None, opens)
    return sessions


def fetch_sehir_tiyatrolari(src, watched):
    base = src["url"].rstrip("/")
    plays = st_list_plays(base)
    kids = st_list_plays(base, category=27)
    if not plays:
        raise RuntimeError("Şehir Tiyatroları'nda hiç oyun bulunamadı")
    plays = {s: p for s, p in plays.items() if s not in kids}
    todo = [s for s, p in plays.items() if not is_watched(p["name"], watched)]
    fetch_sessions(plays, todo, lambda page: st_sessions(page, base))
    return plays


FETCHERS = {"biletinial": fetch_biletinial, "sehir_tiyatrolari": fetch_sehir_tiyatrolari}


# --------------------------------------------------------------------------- durum ve değişiklikler


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def load_watched():
    try:
        with open(WATCHED_FILE, encoding="utf-8") as f:
            lines = [l.strip() for l in f]
    except FileNotFoundError:
        return []
    return [l for l in lines if l and not l.startswith("#")]


def save_watched(names):
    header = (
        "# İzlediğimiz oyunlar: bunlar için bildirim gelmez.\n"
        "# Her satıra bir oyun adı. Telegram'dan /gittik <oyun adı> yazarak da eklenebilir.\n"
    )
    with open(WATCHED_FILE, "w", encoding="utf-8") as f:
        f.write(header + "".join(n + "\n" for n in names))


def stored(play):
    return {
        "name": play["name"],
        "page": play["page"],
        "sessions": {
            sid: {"label": s["label"], "stage": s["stage"], "status": s["status"]}
            for sid, s in sorted(play.get("sessions", {}).items())
        },
    }


def status_text(s):
    if s["status"] == OPEN:
        return "✅ BİLET VAR" + (f" (son {s['remaining']})" if s.get("remaining") else "")
    if s["status"] == FEW:
        return f"⚠️ sadece {s['remaining']} bilet kaldı"
    if s["status"] == SOON:
        return f"⏰ satış {s['opens']}'da açılacak" if s.get("opens") else "⏰ yakında satışta"
    return "❌ tükendi"


def session_line(s):
    where = f" · {s['stage']}" if s.get("stage") else ""
    line = f"• {s['label']}{where}\n  {status_text(s)}"
    if s.get("link"):
        line += f"\n  {s['link']}"
    return line


def diff_source(src_name, old_plays, plays, watched, notified):
    """Bildirilecek değişiklikleri [(başlık, metin)] olarak döndürür.
    notified: {seans_anahtarı: son "açıldı" bildirimi zamanı}, burada güncellenir."""
    changes = []
    now = time.time()
    for slug, play in plays.items():
        if is_watched(play["name"], watched) or "sessions" not in play:
            continue
        old = old_plays.get(slug)
        head = f"🎭 {play['name']} — {src_name}"
        if old is None:
            body = [session_line(s) for s in play["sessions"].values()] or ["(henüz seans yok)"]
            changes.append((f"Yeni oyun: {play['name']}", head + "\n" + "\n".join(body) + f"\n{play['page']}"))
            continue
        lines = []
        for sid, s in play["sessions"].items():
            old_s = old["sessions"].get(sid)
            if old_s is None:
                lines.append("🆕 Yeni seans\n" + session_line(s))
            elif s["status"] == OPEN and old_s["status"] != OPEN:
                key = f"{slug}/{sid}"
                if now - notified.get(key, 0) >= REOPEN_COOLDOWN:
                    notified[key] = now
                    lines.append("🎟 Bilet açıldı!\n" + session_line(s))
        if lines:
            changes.append((f"{play['name']}", head + "\n\n" + "\n\n".join(lines)))
    return changes


# --------------------------------------------------------------------------- bildirim


def telegram_api(method, **params):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    body = urllib.parse.urlencode(params).encode()
    return json.loads(http(f"https://api.telegram.org/bot{token}/{method}", data=body))


def telegram_enabled():
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def notify_telegram(title, body):
    if not telegram_enabled():
        return
    text = f"{title}\n\n{body}" if title not in body else body
    for i in range(0, len(text), 4000):  # Telegram mesaj sınırı 4096 karakter
        telegram_api("sendMessage", chat_id=os.environ["TELEGRAM_CHAT_ID"],
                     text=text[i:i + 4000], disable_web_page_preview="true")
    print("Telegram bildirimi gönderildi")


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
        },
        method="POST",
    )
    print("ntfy bildirimi gönderildi")


def notify_github_issue(title, body):
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    # Telegram/ntfy varsa her bildirim için ayrıca e-posta (issue) gönderme
    if not (token and repo) or telegram_enabled() or os.environ.get("NTFY_TOPIC"):
        return
    http(
        f"https://api.github.com/repos/{repo}/issues",
        data=json.dumps({"title": title[:250], "body": body}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    print("GitHub issue açıldı")


def notify(title, body):
    for fn in (notify_telegram, notify_ntfy, notify_github_issue):
        try:
            fn(title, body)
        except Exception as e:  # bir kanal çökerse diğerleri yine denensin
            print(f"Bildirim hatası ({fn.__name__}): {e}", file=sys.stderr)


# --------------------------------------------------------------------------- Telegram komutları

HELP = (
    "Komutlar:\n"
    "/gittik <oyun adı> – bu oyunu artık takip etme\n"
    "/gerial <oyun adı> – oyunu tekrar takibe al\n"
    "/liste – izlediğimiz (takip edilmeyen) oyunlar"
)


def handle_telegram_commands(state, watched):
    """Bota yazılan komutları işler. izlenenler listesi değiştiyse True döner."""
    if not telegram_enabled():
        return False
    chat_id = str(os.environ["TELEGRAM_CHAT_ID"])
    try:
        updates = telegram_api("getUpdates", offset=state.get("telegram_offset", 0), timeout=0)
    except Exception as e:
        print("Telegram komutları okunamadı:", e, file=sys.stderr)
        return False
    changed = False
    for upd in updates.get("result", []):
        state["telegram_offset"] = upd["update_id"] + 1
        msg = upd.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != chat_id:
            continue
        text = (msg.get("text") or "").strip()
        m = re.match(r"^/?(\w+)(?:@\w+)?\s*(.*)$", text, re.S)
        cmd, arg = (m.group(1).lower(), m.group(2).strip()) if m else ("", "")
        if cmd == "gittik" and arg:
            if normalize(arg) not in {normalize(w) for w in watched}:
                watched.append(arg)
                changed = True
            reply = f"Tamam, “{arg}” artık takip edilmiyor 👍"
        elif cmd in ("gerial", "geri") and arg:
            before = len(watched)
            watched[:] = [w for w in watched if normalize(w) != normalize(arg)]
            changed |= len(watched) != before
            reply = (f"“{arg}” tekrar takipte ✅" if len(watched) != before
                     else f"“{arg}” listede yoktu. /liste ile bakabilirsin.")
        elif cmd == "liste":
            reply = ("İzlediğimiz oyunlar:\n" + "\n".join(f"• {w}" for w in watched)
                     if watched else "Liste boş.")
        else:
            reply = HELP
        try:
            telegram_api("sendMessage", chat_id=chat_id, text=reply)
        except Exception as e:
            print("Telegram cevabı gönderilemedi:", e, file=sys.stderr)
    return changed


# --------------------------------------------------------------------------- ana akış


def main():
    if "--test-notify" in sys.argv:
        notify("Bilet takip testi", "Bildirimler çalışıyor ✅")
        return

    config = load_json(CONFIG_FILE, None)
    if not config:
        sys.exit(f"{CONFIG_FILE} bulunamadı")
    state = load_json(STATE_FILE, {})
    watched = load_watched()

    if handle_telegram_commands(state, watched):
        save_watched(watched)
    watched_norm = [normalize(w) for w in watched]

    old_sources = state.get("sources", {})
    new_sources = {}
    failures = state.get("failures", {})
    all_changes = []
    # 1 günden eski bildirim kayıtlarını temizle
    notified = {
        src: {k: t for k, t in keys.items() if time.time() - t < 86400}
        for src, keys in state.get("notified", {}).items()
    }

    for src in config["sources"]:
        if not src.get("enabled", True):
            continue  # kapalı kaynak; tekrar açılınca ilk tarama sessiz yapılır
        sid, name = src["id"], src["name"]
        old = old_sources.get(sid)
        try:
            plays = FETCHERS[src["type"]](src, watched_norm)
        except Exception as e:
            print(f"[{name}] okunamadı: {e}", file=sys.stderr)
            failures[sid] = failures.get(sid, 0) + 1
            if failures[sid] == FAIL_ALERT_AFTER:
                notify(f"⚠️ {name} okunamıyor",
                       f"{name} yaklaşık 1 saattir okunamıyor. Site değişmiş olabilir.\nHata: {e}")
            if old is not None:
                new_sources[sid] = old
            continue
        failures.pop(sid, None)

        # Atlanan (izlenen) oyunların eski kaydını koru; sayfası okunmadı
        for slug, play in plays.items():
            if "sessions" not in play and old and slug in old:
                play["sessions"] = {k: {**v, "link": ""} for k, v in old[slug]["sessions"].items()}

        print(f"[{name}] {len(plays)} yetişkin oyunu")
        for play in plays.values():
            for s in play.get("sessions", {}).values():
                if s["status"] in (OPEN, SOON):
                    print(f"   {play['name']}: {s['label']} {s['stage']} → {status_text(s)}")

        if old is None:
            print(f"[{name}] ilk tarama: mevcut durum kaydedildi, bildirim gönderilmedi.")
        else:
            all_changes += diff_source(name, old, plays, watched_norm, notified.setdefault(sid, {}))
        new_sources[sid] = {slug: stored(p) for slug, p in sorted(plays.items())}

    if all_changes:
        if len(all_changes) == 1:
            notify(*all_changes[0])
        else:
            notify(f"{len(all_changes)} oyunda değişiklik", "\n\n────────\n\n".join(b for _, b in all_changes))
        for title, _ in all_changes:
            print("Bildirim:", title)
        for _, body in all_changes:
            print(body)
    else:
        print("Değişiklik yok.")

    state["sources"] = new_sources
    state["failures"] = failures
    state["notified"] = {k: v for k, v in notified.items() if v}
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    main()
