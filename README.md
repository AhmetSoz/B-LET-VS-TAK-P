# Bilet Takip

Tiyatro bilet sitelerini 5 dakikada bir kontrol eder ve **Telegram**'dan haber verir.

## Takip edilenler

`config.json` içinde tanımlı. `"enabled": false` olan kaynak takip edilmez; `true` yapınca
ilk taramada sessizce mevcut durumu kaydeder, sonra bildirim göndermeye başlar.

| Kaynak | Site | Durum |
|---|---|---|
| Muammer Karaca Tiyatrosu | [Biletinial](https://biletinial.com/tr-tr/mekan/muammer-karaca-tiyatrosu) | ✅ açık |
| İstanbul Devlet Tiyatrosu | [Biletinial](https://biletinial.com/tr-tr/mekan/istanbul-devlet-tiyatrosu) | ⏸ kapalı |
| İBB Şehir Tiyatroları | [sehirtiyatrolari.ibb.istanbul](https://sehirtiyatrolari.ibb.istanbul/) | ⏸ kapalı |

**Çocuk oyunları takip edilmez** (sitelerin kendi "Çocuk" kategorisine göre). Yetişkin oyunları
ve müzikaller takip edilir.

## Ne zaman bildirim gelir?

- 🎭 **Yeni oyun** eklendiğinde
- 🆕 Bir oyuna **yeni seans** (tarih) eklendiğinde; satış henüz açılmadıysa açılış saati de yazar
- 🎟 **Bilet açıldığında**: tükenmiş bir seansta bilet çıkınca (iade vb.) ya da "Yakında" olan
  seansın satışı başlayınca. Biletinial'de en az 2 bilet kalmış olmalı.

Aynı seans için "bilet açıldı" mesajı en fazla saatte bir gelir.

## İzlediğimiz oyunlar (`izlenenler.txt`)

Bu dosyadaki oyunlar için bildirim gelmez. Telegram'dan bota yazarak da yönetebilirsin:

- `/gittik Hamlet` → Hamlet artık takip edilmez
- `/gerial Hamlet` → tekrar takibe alır
- `/liste` → izlediğimiz oyunlar

Komutlar bir sonraki kontrolde (en geç birkaç dakika içinde) işlenir ve bot cevap verir.

## Yan yana koltuk?

Koltuk haritası Cloudflare robot korumasının arkasında olduğu için otomatik kontrol edilmez.
Bildirim gelince linke tıklayıp koltuğu kendin seçmelisin.

## Kurulum notları

- Repo → Settings → Secrets and variables → Actions: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
  (isteğe bağlı `NTFY_TOPIC`). Telegram/ntfy yoksa GitHub issue (e-posta) ile bildirir.
- Kaç kişilik bilet arandığı: Variables sekmesinde `MIN_TICKETS` (varsayılan 2).
- Repo public olmalı: gizli repolarda ücretsiz Actions süresi 5 dakikalık kontrole yetmez.
- Test: Actions → Bilet takip → Run workflow → "Sadece test bildirimi gönder".
- Yerelde çalıştırma: `python3 monitor.py`
