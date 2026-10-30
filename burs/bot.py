#!/usr/bin/env python3
"""Burs Botu: bursları resmî sayfalardan, kurallarla (yapay zekâ olmadan) takip eder ve e-posta gönderir.

Her çalıştırmada (günde bir):
  1. Sayfa takibi: burslar.json'daki her bursun resmî sayfalarını (watch) okur. Başvuruyla ilgili
     satırlarda değişiklik varsa eklenen satırları, içlerindeki tarihleri ve IELTS puanlarını bildirir.
     Son başvuru tarihi bilinmeyen (ya da geçmiş) bir bursun sayfasında net bir son tarih çıkarsa
     listeyi günceller; henüz geçmemiş bir tarihi asla değiştirmez.
  2. Duyurular: MEB ABDİGM (yabancı devlet bursları) ve YYEGM (YLSY) duyurularındaki yeni burs
     duyurularını bildirir.
  3. Hatırlatma: son başvuruya 30/14/7/3/1/0 gün kala.
  4. Pazartesi: Erasmus Mundus kataloğunda yeni sosyal bilimler programı var mı bakar (programın
     sayfasından IELTS şartını okuyup süzer) ve tam listeyi haftalık özet olarak gönderir.
Gönderecek bir şey yoksa e-posta atmaz. İlk çalıştırmada her kaynağın mevcut hali sessizce kaydedilir.

  python burs/bot.py              -> normal çalışma
  python burs/bot.py --weekly     -> haftalık özeti (ve katalog taramasını) bugün zorla yap
  python burs/bot.py --dry-run    -> e-posta göndermez, burs/onizleme.html'e yazar
"""

import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mail  # noqa: E402

DATA_FILE = mail.DATA
STATE_FILE = mail.HERE / "durum.json"
TODAY = mail.TODAY
REMIND_DAYS = (30, 14, 7, 3, 1, 0)
TARGET_IELTS = 6.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

MONTHS = {}
for i, names in enumerate([
    "january jan ocak gennaio", "february feb şubat subat febbraio", "march mar mart marzo",
    "april apr nisan aprile", "may mayıs mayis maggio", "june jun haziran giugno",
    "july jul temmuz luglio", "august aug ağustos agustos agosto", "september sep sept eylül eylul settembre",
    "october oct ekim ottobre", "november nov kasım kasim novembre", "december dec aralık aralik dicembre",
], 1):
    for n in names.split():
        MONTHS[n] = i
MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))

# Başvuruyla ilgili satırlar: değişiklik karşılaştırması sadece bunlar üzerinden yapılır
RELEVANT = re.compile(
    r"deadline|apply|applica|admission|call|scholarship|grant|funding|ielts|toefl|english|"
    r"open|closed|202[6-9]|başvuru|burs|son tarih|ilan|duyuru|kontenjan|ales|scadenza|"
    rf"\b(?:{MONTH_RE})\b", re.I)
# Bir değişikliğin bildirilmeye değer olması için eklenen satırlarda bunlardan biri olmalı
SIGNAL = re.compile(
    r"deadline|apply|application|call|scholarship|ielts|open|closed|başvuru|burs|son tarih|"
    r"kontenjan|scadenza|\b20(2[6-9])\b", re.I)
# Somut bilgi: bir değişikliğin bildirilmesi için satırda bunlardan biri olmalı
CONCRETE = re.compile(
    r"open|closed|clos|deadline|açıl|başla|kapan|son tarih|son başvuru|kontenjan|ielts|toefl|"
    r"scadenza|€|\$|tl\b|call for", re.I)
# Tek başına tarih/saat satırları (haber listelerindeki "27.10.2026 / 16:24" gibi)
DATE_ONLY = re.compile(r"^[\d./:\s\-–]+$")
DEADLINE_WORDS = re.compile(r"deadline|son başvuru|closes?|closing|apply by|until|scadenza|son tarih", re.I)

# Erasmus Mundus kataloğu süzgeci: sosyal bilimler / insan hakları ile ilgili program adları
STRONG_SOCIAL = re.compile(
    r"human rights|humanitarian|sociolog|migration|migrant|refugee|displacement|gender|feminis|"
    r"peace|democra|citizenship|intercultural|criminolog", re.I)
SOCIAL = re.compile(
    r"human rights|sociolog|social|migration|migrant|refugee|displacement|gender|women|feminis|"
    r"peace|conflict|democra|citizenship|inequal|\bdiversity|intercultural|humanitarian|global studies|"
    r"development|welfare|child|youth|criminolog|anthropolog|policy|politic|society|euroculture|"
    r"memory|minorit|justice|governance", re.I)
NON_SOCIAL = re.compile(
    r"engineer|physic|chemi|materials|robot|energy|nano|marine|aqua|food|agri|hydro|geo|"
    r"photonic|space|quantum|data science|machine|comput|mathemat|biotech|medic|health|pharma|"
    r"neuro|vaccin|nutrition|forest|soil|mining|nuclear|wind|ocean|fish|plant|animal|veterin|"
    r"ecolog|environment|climate|sport|biodivers", re.I)


def is_social(title):
    return bool(STRONG_SOCIAL.search(title) or (SOCIAL.search(title) and not NON_SOCIAL.search(title)))


# --------------------------------------------------------------------------- okuma


def fetch(url, bust_cache=True):
    if bust_cache:  # bayat önbellek kopyası gelmesin (bazı sitelerde sayfalamayı bozduğu için kapatılabilir)
        url += ("&" if "?" in url else "?") + f"_={int(time.time())}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(), r.headers.get("Content-Type", "")


def page_text(raw, ctype):
    if "html" not in ctype and not raw.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        return None  # PDF vb.
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|tr|td|section|dd|dt)>", "\n", text)
    return html.unescape(re.sub(r"<[^>]+>", " ", text))


def relevant_lines(text):
    lines = []
    for line in re.split(r"\n+|(?<=[.!?])\s+(?=[A-ZÇĞİÖŞÜ])", text):
        line = re.sub(r"\s+", " ", line).strip()
        if 12 <= len(line) <= 600 and RELEVANT.search(line) and line not in lines:
            lines.append(line)
    return lines[:400]


def find_dates(text):
    """Metindeki tarihleri ISO biçiminde döndürür (2026–2028 arası)."""
    found = []
    def add(y, m, d):
        try:
            iso = date(int(y), int(m), int(d)).isoformat()
        except ValueError:
            return
        if 2026 <= int(y) <= 2028 and iso not in found:
            found.append(iso)
    low = text.lower()
    for d, m, y in re.findall(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTH_RE})\.?,?\s+(20\d\d)", low):
        add(y, MONTHS[m], d)
    for m, d, y in re.findall(rf"\b({MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(20\d\d)", low):
        add(y, MONTHS[m], d)
    for d, m, y in re.findall(r"\b(\d{1,2})[./](\d{1,2})[./](20\d\d)\b", low):
        add(y, m, d)
    for y, m, d in re.findall(r"\b(20\d\d)-(\d{2})-(\d{2})\b", low):
        add(y, m, d)
    return found


def find_ielts(text):
    scores = []
    # IELTS'ten sonraki ilk puan; araya başka bir sınav adı girerse dur (onun puanını alma)
    for s in re.findall(r"IELTS(?:(?!TOEFL|PTE|Cambridge|Duolingo|TOEIC)[^\n]){0,140}?\b([4-9](?:[.,][05])?)\b(?![.,]?\d)",
                        text, re.I):
        v = float(s.replace(",", "."))
        if 4 <= v <= 9 and v not in scores:
            scores.append(v)
    return scores


# --------------------------------------------------------------------------- yardımcılar


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def save_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def days_left(item):
    d = mail.parse_date(item.get("deadline"))
    return (d - TODAY).days if d else None


def ielts_label(scores):
    if not scores:
        return "IELTS şartı sayfada bulunamadı"
    low = min(scores)
    verdict = "6.0 yetebilir ✅" if low <= TARGET_IELTS else "6.0 yetmez ❌"
    return f"IELTS {', '.join(str(s) for s in scores)} — {verdict}"


# --------------------------------------------------------------------------- 1) sayfa takibi


def check_pages(data, state):
    pages = state.setdefault("pages", {})
    changes = []
    for item in data["items"]:
        for url in item.get("watch", []):
            try:
                raw, ctype = fetch(url)
            except Exception as e:
                rec = pages.setdefault(url, {})
                rec["errors"] = rec.get("errors", 0) + 1
                print(f"Okunamadı ({rec['errors']}. kez): {url} — {str(e)[:120]}", file=sys.stderr)
                continue
            text = page_text(raw, ctype)
            lines = relevant_lines(text) if text is not None else [hashlib.sha1(raw).hexdigest()]
            rec = pages.get(url) or {}
            pages[url] = {"lines": lines, "checked": TODAY.isoformat(), "errors": 0,
                          "changed": rec.get("changed")}
            if "lines" not in rec:
                print(f"İlk kayıt: {url}")
                continue
            old = set(rec["lines"])
            added = [l for l in lines if l not in old]
            if not added:
                continue
            if text is None:
                changes.append((item, "Resmî belge (PDF) değişti, kontrol et.", url))
                pages[url]["changed"] = TODAY.isoformat()
                continue
            signal = [l for l in added if is_signal(l)]
            if not signal:
                print(f"Önemsiz değişiklik: {url}")
                continue
            removed = [l for l in rec["lines"] if l not in set(lines)]
            pages[url]["changed"] = TODAY.isoformat()
            changes.append((item, describe(item, signal, removed), url))
            print(f"Değişti: {url}")
    return changes


def is_signal(line):
    """Bildirilmeye değer satır: başvuruyla ilgili VE somut (tarih, puan, açıldı/kapandı, tutar)."""
    if DATE_ONLY.match(line) or "[...]" in line:
        return False
    return bool(SIGNAL.search(line) and (CONCRETE.search(line) or find_dates(line) or find_ielts(line)))


def describe(item, added, removed=()):
    import difflib
    parts = []
    for line in added[:5]:
        # Aynı satırın eski hali varsa "önce → şimdi" göster (ör. tarih değişmiş)
        best = max(removed, key=lambda r: difflib.SequenceMatcher(None, r, line).ratio(), default=None)
        if best and difflib.SequenceMatcher(None, best, line).ratio() > 0.6:
            parts.append(f"✏️ Önce: {best[:250]}\n➡️ Şimdi: {line[:250]}")
        else:
            parts.append(f"➕ Yeni: {line[:300]}")
    if len(added) > 5:
        parts.append(f"(+{len(added) - 5} satır daha, sayfaya bak)")
    if item.get("deadline"):
        parts.append(f"Listedeki son başvuru: {mail.parse_date(item['deadline']).strftime('%d.%m.%Y')}"
                     + (" (tahmini)" if "tahmin" in (item.get("deadline_note") or "") else ""))
    joined = "\n".join(added)
    dates = [d for d in find_dates(joined) if d >= TODAY.isoformat()]
    if dates:
        parts.append("Yeni satırlardaki tarihler: " + ", ".join(mail.parse_date(d).strftime("%d.%m.%Y") for d in dates))
    scores = find_ielts(joined)
    if scores:
        parts.append(ielts_label(scores))
    # Son tarih: yalnızca "deadline/son başvuru" geçen satırda tek bir gelecek tarih varsa
    candidates = []
    for line in added:
        if DEADLINE_WORDS.search(line):
            ds = [d for d in find_dates(line) if d >= TODAY.isoformat()]
            if len(ds) == 1:
                candidates.append(ds[0])
    left = days_left(item)
    if candidates and (left is None or left < 0):
        new = min(candidates)
        old = item.get("deadline") or "bilinmiyordu"
        item["deadline"] = new
        item["deadline_note"] = f"bot sayfadan okudu ({TODAY.strftime('%d.%m.%Y')}), teyit edin"
        parts.append(f"📅 Listedeki son başvuru tarihi güncellendi: {old} → {mail.parse_date(new).strftime('%d.%m.%Y')}")
    elif candidates and min(candidates) != item.get("deadline"):
        parts.append(f"📅 Sayfada son başvuru tarihi geçiyor: {mail.parse_date(min(candidates)).strftime('%d.%m.%Y')} "
                     f"(listedeki {mail.parse_date(item['deadline']).strftime('%d.%m.%Y')} korunuyor)")
    return "\n".join(parts)


# --------------------------------------------------------------------------- 2) duyurular


def read_announcements(src):
    raw, _ = fetch(src["url"])
    text = raw.decode("utf-8", errors="replace")
    out = []
    if src["type"] == "rss":
        for it in re.findall(r"<item>(.*?)</item>", text, re.S):
            t = re.search(r"<title>(.*?)</title>", it, re.S)
            l = re.search(r"<link>(.*?)</link>", it, re.S)
            if t and l:
                title = html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", t.group(1))).strip()
                out.append((title, l.group(1).strip()))
    else:
        for href, t in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', text, re.S):
            title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t))).strip()
            if len(title) >= 15:
                out.append((title, urllib.parse.urljoin(src["url"], href)))
    inc = re.compile(src["include"], re.I)
    exc = re.compile(src["exclude"], re.I) if src.get("exclude") else None
    return [(t, l) for t, l in out if inc.search(t) and not (exc and exc.search(t))]


def check_announcements(data, state):
    seen = state.setdefault("announcements", {})
    news = []
    for src in data.get("sources", []):
        try:
            items = read_announcements(src)
        except Exception as e:
            print(f"Duyuru kaynağı okunamadı: {src['name']} — {str(e)[:120]}", file=sys.stderr)
            continue
        first = src["name"] not in seen
        known = set(seen.get(src["name"], []))
        for title, link in items:
            if link not in known and not first:
                news.append((src["name"], title, link))
        seen[src["name"]] = (list(known | {l for _, l in items}))[-300:]
        if first:
            print(f"İlk kayıt: {src['name']} ({len(items)} duyuru)")
    return news


# --------------------------------------------------------------------------- 4) Erasmus Mundus kataloğu


CATALOGUE = "https://www.eacea.ec.europa.eu/scholarships/erasmus-mundus-catalogue_en"


def catalogue_page(page):
    """Bir katalog sayfasını okur; site ara sıra boş sayfa döndürdüğü için birkaç kez dener."""
    for attempt in range(4):
        raw, _ = fetch(f"{CATALOGUE}?page={page}", bust_cache=False)
        text = raw.decode("utf-8", errors="replace")
        cards = re.split(r'<article\s+class="ecl-card', text)[1:]
        if cards:
            return text, cards
        time.sleep(3 + attempt * 3)
    return text, []


def read_catalogue():
    """Tüm katalogu okur. Eksik okunursa hata verir: eksik bir liste eski programları 'yeni' gösterirdi."""
    programmes = {}
    first_text, _ = catalogue_page(0)
    total = re.search(r"Projects\s*<span>\s*\((\d+)\)", first_text)
    if not total:
        raise RuntimeError("Katalogdaki toplam program sayısı okunamadı; yapı değişmiş olabilir")
    total = int(total.group(1))
    for page in range((total + 19) // 20):
        _, cards = catalogue_page(page)
        for card in cards:
            m = re.search(r'ecl-content-block__title">\s*<a\s+href="([^"]+)"[^>]*>\s*'
                          r'<span\s+class="ecl-link__label">(.*?)</span>', card, re.S)
            if not m:
                continue
            pid = re.search(r"projects/search/details/(\d+)", card)
            key = pid.group(1) if pid else m.group(1)
            programmes[key] = {"title": re.sub(r"\s+", " ", html.unescape(m.group(2))).strip(), "site": m.group(1)}
    if len(programmes) < total * 0.9:
        raise RuntimeError(f"Katalogdan {total} programın yalnızca {len(programmes)} tanesi okundu")
    return programmes


ADMISSION_LINK = re.compile(r"admission|requirement|eligib|how.to.apply|application|apply|entry|language", re.I)


def site_ielts(site):
    """Programın sitesinde IELTS şartını arar: ana sayfa + başvuru/şartlar alt sayfaları (en fazla 4)."""
    raw, ctype = fetch(site)
    home = raw.decode("utf-8", errors="replace")
    scores = find_ielts(page_text(raw, ctype) or "")
    host = urllib.parse.urlparse(site).netloc
    subpages = []
    for href, label in re.findall(r'<a[^>]+href=["\']?([^"\'\s>#]+)[^>]*>(.*?)</a>', home, re.S):
        url = urllib.parse.urljoin(site, html.unescape(href))
        if urllib.parse.urlparse(url).netloc == host and ADMISSION_LINK.search(href + " " + label) \
                and url not in subpages and not url.lower().endswith(".pdf"):
            subpages.append(url)
    for url in subpages[:4]:
        try:
            raw, ctype = fetch(url)
            scores += [s for s in find_ielts(page_text(raw, ctype) or "") if s not in scores]
        except Exception:
            pass
    return scores


def check_catalogue(state):
    try:
        programmes = read_catalogue()
    except Exception as e:
        print("Erasmus Mundus kataloğu okunamadı:", str(e)[:200], file=sys.stderr)
        return []
    relevant = {pid: p for pid, p in programmes.items() if is_social(p["title"])}
    first = "catalogue" not in state
    known = set(state.get("catalogue", []))
    state["catalogue"] = sorted(known | set(relevant))
    if first:
        print(f"İlk kayıt: Erasmus Mundus kataloğu ({len(relevant)} ilgili program / {len(programmes)})")
        return []
    found = []
    for pid, p in relevant.items():
        if pid in known:
            continue
        try:
            scores = site_ielts(p["site"])
        except Exception:
            scores = []
        project = f"https://erasmus-plus.ec.europa.eu/projects/search/details/{pid}" if pid.isdigit() else p["site"]
        found.append({**p, "ielts": ielts_label(scores), "project": project})
    return found


# --------------------------------------------------------------------------- 3) hatırlatma ve e-posta


def due_reminders(data, state):
    sent = state.setdefault("reminders", {})
    due = []
    for item in data["items"]:
        left = days_left(item)
        if left is None or left not in REMIND_DAYS:
            continue
        key = f"{item['id']}|{item['deadline']}|{left}"
        if key not in sent:
            sent[key] = TODAY.isoformat()
            due.append((item, left))
    state["reminders"] = {k: v for k, v in sent.items() if (TODAY - date.fromisoformat(v)).days < 60}
    return due


def mail_ready():
    return all(os.environ.get(k) for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "MAIL_TO"))


def is_weekly(state):
    week = TODAY.strftime("%G-W%V")
    # Tam liste e-postayla hiç gitmediyse (Gmail sonradan kuruldu) beklemeden ilk çalışmada gönder
    first_mail = mail_ready() and not state.get("weekly_mailed")
    if "--weekly" in sys.argv or first_mail or (TODAY.weekday() == 0 and state.get("last_weekly") != week):
        state["last_weekly"] = week
        if mail_ready():
            state["weekly_mailed"] = week
        return True
    return False


def br(s):
    return mail.esc(s).replace("\n", "<br>")


def section_reminders(due):
    if not due:
        return "", ""
    def when(left):
        return "BUGÜN son gün!" if left == 0 else f"{left} gün kaldı"
    def extra(i):
        return [x for x in (i.get("coverage"), i.get("deadline_note") and "Not: " + i["deadline_note"],
                            i.get("notes")) if x]
    items = "".join(
        f'<li style="margin-bottom:12px"><b>{mail.esc(i["name"])}</b> — '
        f'{mail.parse_date(i["deadline"]).strftime("%d.%m.%Y")} ({when(left)})<br>'
        + "".join(f"{mail.esc(x)}<br>" for x in extra(i))
        + f'Başvuru: <a href="{mail.esc(i.get("link"))}">{mail.esc(i.get("link"))}</a></li>' for i, left in due)
    text = "".join(f"- {i['name']}: {i['deadline']} ({when(left)})\n"
                   + "".join(f"  {x}\n" for x in extra(i)) + f"  {i.get('link', '')}\n" for i, left in due)
    return f"<h3>⏰ Son başvuru hatırlatması</h3><ul>{items}</ul>", f"HATIRLATMA\n{text}\n"


def section_changes(changes):
    if not changes:
        return "", ""
    items = "".join(
        f'<li style="margin-bottom:10px"><b>{mail.esc(i["name"])}</b><br>{br(n)}<br>'
        f'<a href="{mail.esc(u)}">Sayfayı aç →</a></li>' for i, n, u in changes)
    text = "".join(f"- {i['name']}\n{n}\n{u}\n\n" for i, n, u in changes)
    return f"<h3>🔔 Resmî sayfalarda değişiklik</h3><ul>{items}</ul>", f"DEĞİŞİKLİKLER\n{text}"


def section_news(news):
    if not news:
        return "", ""
    items = "".join(f'<li style="margin-bottom:6px">{mail.esc(t)} <span style="color:#57606a">({mail.esc(s)})</span><br>'
                    f'<a href="{mail.esc(l)}">Duyuruyu aç →</a></li>' for s, t, l in news)
    text = "".join(f"- {t} ({s}) {l}\n" for s, t, l in news)
    return f"<h3>📢 Yeni burs duyuruları</h3><ul>{items}</ul>", f"YENİ DUYURULAR\n{text}\n"


def section_catalogue(found):
    if not found:
        return "", ""
    items = "".join(
        f'<li style="margin-bottom:8px"><b>{mail.esc(f["title"])}</b><br>{mail.esc(f["ielts"])}<br>'
        f'<a href="{mail.esc(f["site"])}">Program sitesi →</a> · <a href="{mail.esc(f["project"])}">AB proje sayfası</a></li>'
        for f in found)
    text = "".join(f"- {f['title']} — {f['ielts']} {f['site']}\n" for f in found)
    return ("<h3>🆕 Erasmus Mundus kataloğuna eklenen sosyal bilimler programları</h3>"
            "<p style=\"color:#57606a;font-size:13px\">Tam burslu (ayda €1.400 + ücret muafiyeti); "
            "Türk vatandaşları başvurabilir. IELTS bilgisi programın sitesinden otomatik okundu.</p>"
            f"<ul>{items}</ul>", f"YENİ ERASMUS MUNDUS PROGRAMLARI\n{text}\n")


def deliver(subject, text_body, html_body):
    """Bildirimler yalnızca e-postayla gider."""
    if not mail_ready():
        raise RuntimeError("Gmail ayarlı değil (GMAIL_USER / GMAIL_APP_PASSWORD / MAIL_TO); e-posta gönderilemedi")
    mail.send(subject, text_body, html_body)


def main():
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    state = load_json(STATE_FILE, {})

    changes = check_pages(data, state)
    news = check_announcements(data, state)
    due = due_reminders(data, state)
    weekly = is_weekly(state)
    found = check_catalogue(state) if weekly or "catalogue" not in state else []

    parts = [section_reminders(due), section_news(news), section_catalogue(found), section_changes(changes)]
    extra_html = "".join(h for h, _ in parts)
    extra_text = "".join(t for _, t in parts)

    if weekly:
        subject, text_body, html_body = mail.build(data, extra_html=extra_html, extra_text=extra_text)
    elif due or news or found or changes:
        if due:
            first, left = min(due, key=lambda x: x[1])
            subject = f"⏰ Burs hatırlatma: {first['name']} — " + ("BUGÜN son gün" if left == 0 else f"{left} gün kaldı")
        elif news:
            subject = f"📢 Yeni burs duyurusu: {news[0][1]}" + (f" (+{len(news) - 1})" if len(news) > 1 else "")
        else:
            n = len(changes) + len(found)
            subject = f"🔔 Burs güncellemesi ({n})"
        html_body = mail.wrap(subject, extra_html)
        text_body = extra_text
    else:
        subject = None
        print("Gönderilecek bir şey yok.")

    if subject:
        print("E-posta:", subject)
        if "--dry-run" in sys.argv:
            (mail.HERE / "onizleme.html").write_text(html_body, encoding="utf-8")
            print("(dry-run) burs/onizleme.html yazıldı")
        else:
            deliver(subject, text_body, html_body)

    # E-posta gönderildikten sonra kaydet: gönderim hata verirse bir sonraki gün tekrar denenir
    save_json(DATA_FILE, data)
    save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()
