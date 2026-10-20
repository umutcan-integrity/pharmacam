# PharmaCam videoları (ft-motion)

Bu klasördeki sahneler [ft-motion](../../ft-motion) motoruyla render edilir: her kare zamanın saf bir
fonksiyonu (`draw(ctx, t)`), headless Chrome'da hareket bulanıklığıyla çizilir, ffmpeg ile kodlanır,
ses `sound.py` ile aynı zaman çizelgesinde sentezlenir.

| Proje | Süre | Kullanım |
|---|---|---|
| `pharmacam-film` | 20 sn, 1920×1080, 60 fps, sesli | Sitede "Film" bölümü; reklam ana kurgusu |
| `pharmacam-hero` | 8 sn, kesintisiz döngü, sessiz | Sitenin en üstündeki arka plan |

Konsept "Kapıdan tezgâha": tavandan görünen eczane planında bir müşterinin yolculuğu (kapı → raf →
sıra → tezgâh), personel ayrımı, ısı haritası, gizlilik dönüşü (görüntü → çip → yalnızca sayılar),
panel ve tahmin, kapanış kartı. Ekrandaki sayılar (452 giren, 7 içeride, 1 dk 04 sn, ≈ 477) demo değerleridir.

## Render

```bash
# ft-motion kökünde (Node 18+, Python numpy/scipy, ffmpeg, Chrome)
cp -r <bu-repo>/video/pharmacam-film <bu-repo>/video/pharmacam-hero examples/
node ft.mjs preview examples/pharmacam-film          # canlı önizleme
python examples/pharmacam-film/sound.py              # ses → out/audio.wav (-14 LUFS)
node ft.mjs render examples/pharmacam-film           # → out/pharmacam-film.mp4
node ft.mjs render examples/pharmacam-film --lang en # İngilizce sürüm
node ft.mjs render examples/pharmacam-hero
```

`pharmacam-hero/scene.js`, planı `../pharmacam-film/plan.js` dosyasından alır; iki klasör yan yana durmalı.

## Reklam formatları

Sahne yerleşimi tuvalin oranına göre kendini ayarlar (yatayda metin solda, kare/dikeyde üstte).
Instagram/TikTok için `project.json` içinde `width`/`height` değerlerini değiştirip yeniden render edin:

| Format | width × height |
|---|---|
| Instagram akışı | 1080 × 1080 veya 1080 × 1350 |
| Reels / Stories / TikTok | 1080 × 1920 |

Kare ve dikey formatlar henüz kare kare kontrol edilmedi; render sonrası `node ft.mjs sheet` ile bakın.
