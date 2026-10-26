#!/usr/bin/env python3
"""Burs Botu: burslar.json'daki bursları düzenli olarak takip eder ve e-posta gönderir.

Her çalıştırmada (günde bir):
  * Her bursun resmî sayfalarını okur. Başvuruyla ilgili kısım (tarih, IELTS, başvuru,
    burs cümleleri) değiştiyse Gemini'ye okutup ne değiştiğini özetletir; son başvuru
    tarihi açıklandıysa listeyi günceller.
  * Son başvuruya 30/14/7/3/1/0 gün kalan burslar için hatırlatma hazırlar.
  * Haftada bir (pazartesi) Gemini + Google Arama ile listede olmayan yeni bursları arar ve
    tam listeyle birlikte haftalık özet gönderir.
Gönderecek bir şey yoksa e-posta atmaz.

Ortam değişkenleri: GMAIL_USER, GMAIL_APP_PASSWORD, MAIL_TO (bkz. mail.py),
GEMINI_API_KEY (yoksa Gemini adımları atlanır), GEMINI_MODEL (varsayılan gemini-flash-latest).

  python burs/bot.py              -> normal çalışma
  python burs/bot.py --weekly     -> haftalık özeti bugün zorla gönder
  python burs/bot.py --dry-run    -> e-posta göndermez, burs/onizleme.html'e yazar
"""

import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.request
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mail  # noqa: E402

DATA_FILE = mail.DATA
STATE_FILE = mail.HERE / "durum.json"
TODAY = mail.TODAY
REMIND_DAYS = (30, 14, 7, 3, 1, 0)
GEMINI_MODEL = os.environ.get("GEMINI_MODEL") or "gemini-flash-latest"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
# Sayfanın sadece başvuruyla ilgili cümlelerini izliyoruz; menü/haber kaymaları değişiklik sayılmasın
KEYWORDS = re.compile(
    r"deadline|apply|applica|admission|call|scholarship|grant|funding|ielts|toefl|english|"
    r"open|closed|202[6-9]|başvuru|burs|son tarih|ilan|duyuru|kontenjan|dil|ales|"
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
    r"ocak|şubat|mart|nisan|mayıs|haziran|temmuz|ağustos|eylül|ekim|kasım|aralık",
    re.I,
)


# --------------------------------------------------------------------------- sayfa okuma


def fetch_text(url):
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(f"{url}{sep}_={int(time.time())}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        ctype = r.headers.get("Content-Type", "")
    if "html" not in ctype and not raw.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        return hashlib.sha1(raw).hexdigest()  # PDF vb.: sadece içerik değişti mi
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|tr|td|section)>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return text


def relevant(text):
    """Başvuruyla ilgili satırlar; değişiklik karşılaştırması bunun üzerinden yapılır."""
    lines = []
    for line in re.split(r"\n+|(?<=[.!?])\s+(?=[A-ZÇĞİÖŞÜ])", text):
        line = re.sub(r"\s+", " ", line).strip()
        if 12 <= len(line) <= 600 and KEYWORDS.search(line) and line not in lines:
            lines.append(line)
    return "\n".join(lines)[:12000]


# --------------------------------------------------------------------------- Gemini


def gemini(prompt, search=False):
    """Gemini'ye sorar, cevaptaki JSON'u döndürür. Anahtar yoksa ya da hata olursa None."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    if search:
        body["tools"] = [{"google_search": {}}]  # arama aracıyla JSON modu birlikte kullanılamıyor
    else:
        body["generationConfig"] = {"responseMimeType": "application/json", "temperature": 0.1}
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.load(r)
        parts = resp["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts)
        m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
        text = m.group(1) if m else text
        start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=0)
        return json.loads(text[start:])
    except Exception as e:
        print("Gemini hatası:", str(e)[:300], file=sys.stderr)
        return None


PROFILE = (
    "Aday: Türk vatandaşı, IELTS Academic 6.0, sosyoloji / insan hakları (ayrıca göç, toplumsal "
    "cinsiyet, sosyal politika) alanında 2027'de başlayacak TAM burslu (okul ücreti + yaşam bursu) "
    "yüksek lisans arıyor."
)


def analyze_change(item, old, new):
    prompt = f"""{PROFILE}
Bugün {TODAY.isoformat()}. Aşağıdaki burs/program sayfasının başvuruyla ilgili kısmı değişti.
Burs: {item['name']} ({item.get('where', '')})
Bilinen son başvuru tarihi: {item.get('deadline') or 'bilinmiyor'} — {item.get('deadline_note') or ''}

ESKİ METİN:
{old[:6000]}

YENİ METİN:
{new[:6000]}

Adayı ilgilendiren gerçek bir değişiklik var mı? (başvuru açıldı/kapandı, yeni ya da değişen son
başvuru tarihi, İngilizce şartı, burs miktarı, kontenjan). Tarih/haber/menü kaymalarını önemsiz say.
Sadece şu JSON'u döndür:
{{"onemli": true/false,
  "ozet": "Türkçe, en fazla 2 cümle",
  "deadline": "YYYY-MM-DD ya da null (yalnızca 2027 başlangıcı için kesin son başvuru tarihi metinde açıkça yazıyorsa)",
  "deadline_note": "kısa Türkçe not ya da null"}}"""
    return gemini(prompt)


def discover(known_names):
    prompt = f"""{PROFILE}
Bugün {TODAY.isoformat()}. Google'da arayarak bu kritere uyan, şu an başvurusu açık ya da yakında
açılacak burs/programları bul. Şunlar ZATEN listemizde, bunları tekrar önerme: {', '.join(known_names)}.
Yalnızca resmî kaynağı (üniversite, bakanlık, büyükelçilik, burs kurumu sitesi) olanları öner,
IELTS 6.0'ın yetip yetmediğini belirt. En fazla 5 sonuç.
Sadece JSON dizi döndür, başka metin yazma:
[{{"name": "...", "where": "ülke / üniversite", "coverage": "neleri karşılıyor",
   "ielts": "İngilizce şartı ve 6.0 yeterli mi", "deadline": "YYYY-MM-DD ya da null",
   "link": "resmî sayfa", "why": "neden uygun, 1 cümle"}}]"""
    res = gemini(prompt, search=True)
    return res if isinstance(res, list) else []


# --------------------------------------------------------------------------- yardımcılar


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def save_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def norm(s):
    s = (s or "").translate(str.maketrans("İIıÇçĞğÖöŞşÜü", "iiiccggoossuu")).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


GENERIC = set("""scholarship scholarships programme program master masters university universitat
international government human rights studies burs bursu bursları yuksek lisans devlet erasmus mundus
and the for with of in ve ile""".split())


def similar(a, b):
    """Aynı bursun farklı yazılışlarını yakalar ('Stipendium Hungaricum Scholarship' ~ 'Stipendium Hungaricum')."""
    a, b = norm(a), norm(b)
    if not (a and b):
        return False
    if a in b or b in a:
        return True
    la = [w for w in a.split() if len(w) > 2 and w not in GENERIC]
    lb = [w for w in b.split() if len(w) > 2 and w not in GENERIC]
    common = set(la) & set(lb)
    # İki ortak ayırt edici kelime ya da ortak "ilk" kelime (Fulbright, NOHA, EMMIR gibi özel adlar)
    return len(common) >= 2 or bool(la and lb and (la[0] in common or lb[0] in common))


def days_left(item):
    d = mail.parse_date(item.get("deadline"))
    return (d - TODAY).days if d else None


# --------------------------------------------------------------------------- ana akış


def check_pages(data, state):
    """Sayfaları kontrol eder; [(item, özet)] değişiklik listesi döndürür."""
    pages = state.setdefault("pages", {})
    changes = []
    for item in data["items"]:
        for url in item.get("watch", []):
            try:
                rel = relevant(fetch_text(url))
            except Exception as e:
                rec = pages.setdefault(url, {})
                rec["errors"] = rec.get("errors", 0) + 1
                print(f"Okunamadı ({rec['errors']}. kez): {url} — {str(e)[:120]}", file=sys.stderr)
                continue
            digest = hashlib.sha1(rel.encode()).hexdigest()
            rec = pages.get(url)
            pages[url] = {"hash": digest, "text": rel, "checked": TODAY.isoformat(), "errors": 0,
                          "changed": (rec or {}).get("changed")}
            if rec is None or not rec.get("hash"):
                print(f"İlk kayıt: {url}")
                continue
            if rec["hash"] == digest:
                continue
            pages[url]["changed"] = TODAY.isoformat()
            print(f"Değişti: {url}")
            res = analyze_change(item, rec.get("text", ""), rel)
            if res is None:  # Gemini yok/hata: değişikliği yine de bildir
                changes.append((item, f"Resmî sayfada başvuruyla ilgili bir değişiklik var, kontrol et: {url}"))
                continue
            if not res.get("onemli"):
                print("  önemsiz:", res.get("ozet"))
                continue
            note = res.get("ozet") or "Sayfada önemli bir değişiklik var."
            new_deadline = res.get("deadline")
            old_left = days_left(item)
            if mail.parse_date(new_deadline) and new_deadline != item.get("deadline"):
                if old_left is not None and old_left >= 0 and new_deadline > item["deadline"]:
                    # Henüz geçmemiş daha erken bir tarih var (ör. 1. başvuru dönemi): onu kaybetme
                    note += f" Sayfada daha geç bir tarih de var: {new_deadline} (listedeki {item['deadline']} korunuyor)."
                else:
                    old = item.get("deadline") or "bilinmiyordu"
                    item["deadline"] = new_deadline
                    item["deadline_note"] = (res.get("deadline_note") or "") + f" (bot {TODAY.strftime('%d.%m.%Y')} güncelledi)"
                    note += f" Son başvuru tarihi: {old} → {new_deadline}."
            changes.append((item, note + f"\n{url}"))
    return changes


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
    # 60 günden eski kayıtları temizle
    state["reminders"] = {k: v for k, v in sent.items() if (TODAY - date.fromisoformat(v)).days < 60}
    return due


def is_weekly(state):
    week = TODAY.strftime("%G-W%V")
    if "--weekly" in sys.argv or (TODAY.weekday() == 0 and state.get("last_weekly") != week):
        state["last_weekly"] = week
        return True
    return False


def section_changes(changes):
    if not changes:
        return "", ""
    items = "".join(
        f'<li style="margin-bottom:8px"><b>{mail.esc(i["name"])}</b><br>{mail.esc(n).replace(chr(10), "<br>")}</li>'
        for i, n in changes)
    text = "".join(f"- {i['name']}: {n}\n" for i, n in changes)
    return (f'<h3>🔔 Sayfalarda değişiklik</h3><ul>{items}</ul>', f"DEĞİŞİKLİKLER\n{text}\n")


def section_reminders(due):
    if not due:
        return "", ""
    def when(left):
        return "BUGÜN son gün!" if left == 0 else f"{left} gün kaldı"
    items = "".join(
        f'<li style="margin-bottom:6px"><b>{mail.esc(i["name"])}</b> — '
        f'{mail.parse_date(i["deadline"]).strftime("%d.%m.%Y")} ({when(left)})<br>'
        f'<a href="{mail.esc(i.get("link"))}">Başvuru sayfası →</a></li>' for i, left in due)
    text = "".join(f"- {i['name']}: {i['deadline']} ({when(left)}) {i.get('link', '')}\n" for i, left in due)
    return (f'<h3>⏰ Son başvuru hatırlatması</h3><ul>{items}</ul>', f"HATIRLATMA\n{text}\n")


def section_discoveries(found):
    if not found:
        return "", ""
    items = "".join(
        f'<li style="margin-bottom:8px"><b>{mail.esc(f.get("name"))}</b> — {mail.esc(f.get("where"))}<br>'
        f'{mail.esc(f.get("coverage"))}<br>İngilizce: {mail.esc(f.get("ielts"))}<br>'
        f'Son başvuru: {mail.esc(f.get("deadline") or "belirtilmemiş")}<br>{mail.esc(f.get("why"))}<br>'
        f'<a href="{mail.esc(f.get("link"))}">{mail.esc(f.get("link"))}</a></li>' for f in found)
    text = "".join(f"- {f.get('name')} ({f.get('where')}): {f.get('link')}\n" for f in found)
    return (f'<h3>🔎 Bu hafta bulunan yeni olası burslar</h3>'
            f'<p style="color:#9a6700;font-size:13px">Gemini Google aramasıyla buldu, doğrulanmadı. '
            f'Başvurmadan önce resmî sayfadan kontrol edin.</p><ul>{items}</ul>',
            f"YENİ OLASI BURSLAR (doğrulanmadı)\n{text}\n")


def main():
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    state = load_json(STATE_FILE, {})

    changes = check_pages(data, state)
    due = due_reminders(data, state)
    weekly = is_weekly(state)

    found = []
    if weekly:
        known = [n for i in data["items"] for n in [i["name"], *i.get("aliases", [])]] + state.get("suggested", [])
        found = [f for f in discover(known) if f.get("name") and not any(similar(f["name"], k) for k in known)]
        state["suggested"] = (state.get("suggested", []) + [f["name"] for f in found])[-100:]

    ch_html, ch_text = section_changes(changes)
    rem_html, rem_text = section_reminders(due)
    new_html, new_text = section_discoveries(found)

    if weekly:
        subject, text_body, html_body = mail.build(
            data, extra_html=rem_html + ch_html + new_html, extra_text=rem_text + ch_text + new_text)
    elif changes or due:
        if due:
            first, left = min(due, key=lambda x: x[1])
            subject = f"⏰ Burs hatırlatma: {first['name']} — " + ("BUGÜN son gün" if left == 0 else f"{left} gün kaldı")
        else:
            subject = f"🔔 Burs güncellemesi: {changes[0][0]['name']}" + (f" (+{len(changes) - 1})" if len(changes) > 1 else "")
        html_body = mail.wrap(subject, rem_html + ch_html)
        text_body = rem_text + ch_text
    else:
        subject = None
        print("Gönderilecek bir şey yok.")

    if subject:
        print("E-posta:", subject)
        if "--dry-run" in sys.argv:
            (mail.HERE / "onizleme.html").write_text(html_body, encoding="utf-8")
            print("(dry-run) burs/onizleme.html yazıldı")
        else:
            mail.send(subject, text_body, html_body)

    # E-posta gönderildikten sonra kaydet: gönderim hata verirse hatırlatma bir sonraki gün tekrar denenir
    save_json(DATA_FILE, data)
    save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()
