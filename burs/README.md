# Burs Botu

Sosyoloji / insan hakları alanında, IELTS 6.0 ile başvurulabilecek **tam burslu yüksek lisans**
seçeneklerini takip eder ve e-posta gönderir. Yapay zekâ kullanmaz: her şeyi resmî sitelerden okur ve
kurallarla süzer. GitHub Actions'ta her gün çalışır (`.github/workflows/burs-mail.yml`).

| Ne zaman | Ne yapar |
|---|---|
| Her gün | `burslar.json`'daki her bursun resmî sayfalarını (`watch`) okur. Başvuruyla ilgili satırlara yeni bir şey eklendiyse (tarih, başvuru, IELTS…) eklenen satırları, içlerindeki tarihleri ve IELTS puanlarını e-postayla bildirir. |
| Her gün | MEB ABDİGM (yabancı devlet bursları) ve YYEGM (YLSY) duyurularında yeni burs duyurusu var mı bakar (`sources`). |
| Her gün | Son başvuruya 30 / 14 / 7 / 3 / 1 gün kala ve son gün hatırlatır. |
| Pazartesi | Erasmus Mundus resmî kataloğuna yeni bir sosyal bilimler programı eklendiyse, programın sitesinden IELTS şartını okuyup "6.0 yetebilir / yetmez" diye süzer; tam listeyi haftalık özet olarak gönderir. |

Gönderecek bir şey yoksa e-posta atmaz. Son başvuru tarihi bilinmeyen bir bursun sayfasında
"deadline / son başvuru" satırında tek bir tarih çıkarsa listeye yazar; henüz geçmemiş bir tarihi asla değiştirmez.

Dosyalar: `burslar.json` (liste ve duyuru kaynakları), `durum.json` (botun hafızası),
`rapor.md` (ilk araştırma raporu), `bot.py` (takip), `mail.py` (e-posta).

## Gerekli GitHub secret'ları

| Secret | Değer |
|---|---|
| `GMAIL_USER` | gönderen Gmail adresi |
| `GMAIL_APP_PASSWORD` | o hesabın 16 haneli uygulama şifresi |
| `BURS_MAIL_TO` | alıcılar, virgülle ayrılmış |

Elle çalıştırma: Actions → **Burs botu** → Run workflow ("Haftalık özeti şimdi gönder" ile tam liste).
Yerelde deneme: `python burs/bot.py --dry-run --weekly` (göndermez, `burs/onizleme.html` yazar).
