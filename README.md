# Bilet Takip – Muammer Karaca Tiyatrosu

[Biletinial'deki Muammer Karaca Tiyatrosu sayfasını](https://biletinial.com/tr-tr/mekan/muammer-karaca-tiyatrosu)
her 10 dakikada bir kontrol eder. Listeye **yeni bir oyun** eklendiğinde (yani biletleri satışa açıldığında)
sana bildirim gönderir.

GitHub Actions üzerinde ücretsiz çalışır, bilgisayarının açık olmasına gerek yok.

## Nasıl çalışır?

1. `monitor.py` mekan sayfasını indirir ve listelenen oyunları çıkarır.
2. `state.json` içindeki bir önceki listeyle karşılaştırır.
3. Yeni bir oyun varsa açık olan tüm bildirim kanallarına haber verir ve listeyi kaydeder.

## Bildirim kanalları

Hiçbir ayar yapmasan da **GitHub issue** açılır ve GitHub sana e-posta atar
(repo'yu "Watch" ediyorsan; repo sahibi olarak varsayılan olarak ediyorsun).
Telefona anında bildirim istiyorsan aşağıdakilerden birini (ya da ikisini) kur:

### A) ntfy – en kolayı (hesap gerekmez)

1. Telefonuna **ntfy** uygulamasını kur (Android / iOS).
2. Uygulamada tahmin edilmesi zor bir konu (topic) adına abone ol, ör. `muammer-karaca-bilet-x7k29q`.
3. GitHub'da repo → **Settings → Secrets and variables → Actions → New repository secret**:
   - `NTFY_TOPIC` = seçtiğin konu adı

### B) Telegram

1. Telegram'da **@BotFather**'a `/newbot` yaz, bot oluştur ve verdiği token'ı al.
2. Botuna bir mesaj at, sonra `https://api.telegram.org/bot<TOKEN>/getUpdates` adresini aç ve `chat.id` değerini bul.
3. Repo secret'ları olarak ekle:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

## Test

Repo → **Actions → Bilet takip → Run workflow** → "Sadece test bildirimi gönder" kutusunu işaretle → Run.
Birkaç saniye içinde test bildirimi gelmeli.

## Notlar

- GitHub, zamanlanmış görevleri yoğun saatlerde birkaç dakika geciktirebilir.
- Zamanlanmış görevler yalnızca repo'nun **varsayılan dalında** (default branch) çalışır.
- Başka bir mekanı takip etmek için workflow'a `VENUE_URL` ortam değişkeni ekleyebilirsin.
- Yerelde çalıştırmak için: `python3 monitor.py`
