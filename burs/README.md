# Burs Botu

Sosyoloji / insan hakları alanında, IELTS 6.0 ile başvurulabilecek **tam burslu yüksek lisans**
seçeneklerini takip eder ve e-posta gönderir. GitHub Actions'ta her gün çalışır (`.github/workflows/burs-mail.yml`).

- **Her gün:** `burslar.json`'daki her bursun resmî sayfasını (`watch`) okur. Başvuruyla ilgili kısım
  değiştiyse Gemini'ye okutur; önemliyse e-posta atar, yeni son başvuru tarihi açıklandıysa listeyi günceller.
- **Hatırlatma:** son başvuruya 30 / 14 / 7 / 3 / 1 gün kala ve son gün.
- **Her pazartesi:** tam liste + Gemini'nin Google aramasıyla bulduğu yeni olası burslar.
- Gönderecek bir şey yoksa e-posta atmaz.

Dosyalar: `burslar.json` (liste), `durum.json` (botun hafızası), `rapor.md` (ilk araştırma raporu),
`bot.py` (takip), `mail.py` (e-posta).

## Gerekli GitHub secret'ları

| Secret | Değer |
|---|---|
| `GMAIL_USER` | gönderen Gmail adresi |
| `GMAIL_APP_PASSWORD` | o hesabın 16 haneli uygulama şifresi |
| `BURS_MAIL_TO` | alıcılar, virgülle ayrılmış |
| `GEMINI_API_KEY` | aistudio.google.com'dan ücretsiz API anahtarı (yoksa Gemini adımları atlanır) |

Elle çalıştırma: Actions → **Burs botu** → Run workflow ("Haftalık özeti şimdi gönder" ile tam liste).
Yerelde deneme: `python burs/bot.py --dry-run --weekly` (göndermez, `burs/onizleme.html` yazar).

Bursa yeni bir program eklemek için `burslar.json`'a aynı biçimde bir kayıt ekleyin.
