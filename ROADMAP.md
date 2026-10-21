# PharmaCam — Ürün Yol Haritası

PharmaCam, eczanelere kurulan **Raspberry Pi 5 + Raspberry Pi AI Camera (Sony IMX500)** tabanlı bir
mağaza içi analitik cihazıdır. Yapay zekâ modeli kameranın kendi çipinde çalışır; Pi yalnızca
kutuları takip eder, analiz eder ve veriyi saklar. **Görüntü kaydedilmez, yüz tanıma yapılmaz.**

## Mimari

```
┌──────────────────────── Eczane ────────────────────────┐        ┌──────── Bulut (opsiyonel) ───────┐
│  IMX500 AI Camera ──► Raspberry Pi 5 (pharmacam-edge)   │        │  pharmacam-hub (FastAPI+SQLite)   │
│   (kişi tespiti      ├─ Takip (ByteTrack-lite)           │ HTTPS  │  ├─ Çoklu eczane özeti            │
│    çip üzerinde)     ├─ Analitik motoru                  │ ─────► │  ├─ Cihaz sağlık / heartbeat      │
│                      │   giriş-çıkış, sıra, raf, ısı     │  sync  │  └─ Merkezi geçmiş veri           │
│                      ├─ Beyaz önlük (çalışan) ayrımı     │        └───────────────────────────────────┘
│                      ├─ SQLite (geçmiş veri)             │
│                      ├─ Günlük PDF rapor + tahmin        │
│                      └─ Web paneli :8080                 │
└─────────────────────────────────────────────────────────┘
```

## Faz 0 — Donanım doğrulama ✅
- [x] Pi 5 + IMX500 tanınıyor, `imx500-all` modelleri kurulu
- [x] Çip üzerinde nesne tespiti çalışıyor: YOLO11n 640 (COCO mAP 0,374), 15 kare/sn'de her kare analiz
- [x] SSH anahtar erişimi

## Faz 1 — Çekirdek analitik (MVP) ✅
- [x] Kamera kaynağı soyutlaması: IMX500 (gerçek) + simülatör (geliştirme/test/demo)
- [x] Kişi takibi: iki aşamalı IoU eşleştirmeli ByteTrack-lite, sabit hız tahmini
- [x] **Giriş/çıkış sayımı**: kapı çizgisi + histerezis (çift sayım yok)
- [x] **Anlık içerideki kişi**: görünen müşteri + sayaç (hibrit, kayma düzeltmeli)
- [x] **Raf önünde durma analizi**: bölge bazlı ziyaret, toplam/ortalama bekleme süresi
- [x] **Sıra analizi**: sıra uzunluğu, sırada bekleme süresi, hizmet süresi, sıradan vazgeçenler
- [x] **Isı haritası**: ayak noktası yoğunluğu, saatlik kayıt, arka plan üzerine bindirme
- [x] **Çalışan ayrımı**: gövde bölgesinde beyaz önlük oranı (HSV) + tezgâh arkası bölgesi oylaması
- [x] **Anonim ziyaret kaydı**: her ziyaret için giriş/çıkış saati, süre, gezilen bölgeler, sıra süresi
- [x] **Kişi tahminleme**: haftanın günü × saat bazlı ziyaretçi tahmini (7 gün ileri, güven aralıklı)
- [x] SQLite veri katmanı (WAL): ziyaretler, olaylar, dakikalık metrikler, bölge ve ısı haritası saatlikleri

## Faz 2 — Uygulama ✅
- [x] Web paneli (Türkçe): Canlı, Analiz, Isı Haritası, Raflar, Tahmin, Raporlar, Bölge Ayarları
- [x] Görsel bölge editörü (kamera görüntüsü üzerine çizim)
- [x] Canlı görüntü (opsiyonel, kişiler bulanıklaştırılabilir)
- [x] Günlük PDF raporu (grafikler + ısı haritası + yarın tahmini), her gün kapanışta otomatik
- [x] REST API (tüm veriler JSON)
- [x] Panel şifresi (HTTP Basic)

## Faz 3 — Bağlantılı ürün ✅
- [x] Buluta senkronizasyon (kuyruklu, internet kesilince veri kaybı yok)
- [x] `pharmacam-hub`: çoklu eczane merkezi paneli, cihaz anahtarı ile kimlik doğrulama, Docker
- [x] systemd servisleri, otomatik yeniden başlatma, kamera bekçisi (watchdog)
- [x] Tek komutla kurulum betiği (`deploy/install.sh`)
- [x] Görselli PDF kurulum kılavuzu (`docs/PharmaCam-Kurulum-Kilavuzu.pdf`)

## Faz 3.5 — Doğruluk ve müşteri profili ✅ (2026-10-21)
- [x] Takip v2: Kalman + üç aşamalı Hungarian + son görüldüğü yerden yakalama (simülatörde Roboflow/Sony ByteTrack ile kıyaslandı)
- [x] Poster/manken eleme, "yok say" bölgesi, günlük müşteri sıra no, kıyafet rengiyle etiket koruma (yalnız 30 sn, yalnız etiket)
- [x] Cam tasarımlı canlı görünüm: kamera modunda kutu + etiket, görüntüsüz modda plan üzerinde nokta
- [x] Kurulum asistanı: ışık, netlik, eğim, tespit güveni, güç kaynağı, kapı çizgisi önerisi, önlük ölçümü
- [x] Sayım doğruluğu testi: telefondan elle sayım, kişi kişi (±6 sn) eşleştirme, kayıtlı sonuç
- [x] Yaş/cinsiyet (isteğe bağlı, varsayılan kapalı): MiVOLO v2 yüz + beden, int8, Pi 5'te kişi başı ~1,3 sn;
      elle etiketlenmiş videoda yüz ≥32 px'te cinsiyet %93; ziyaret başına oylama, emin değilse "belirsiz"

## Faz 4 — Saha pilotu (sıradaki)
- [ ] Yaş/cinsiyet: gerçek eczane görüntüsünde ~300 etiketli ziyaretle kalibrasyon (rıza alınmış), sonuçların
      karışıklık matrisiyle düzeltilmesi; başörtüsü/şapka, 60+ yaş ayrıca ölçülmeli
- [ ] Yaş/cinsiyet için kesitleri 2028×1520 akıştan almak (yüzler 1,6 kat büyür; CPU etkisi ölçülmeli)
- [ ] Hub: müşteri profili (cinsiyet/yaş) senkronu ve şube karşılaştırması
- [ ] Hukuki görüş: KVKK'da şablonsuz yaş/cinsiyet tahmininin dayanağı (m.5/2-f mi, açık rıza mı)
- [ ] İlk eczanede 2 hafta pilot; kapı sayımının elle sayımla karşılaştırılması (hedef ≥ %95)
- [ ] Önlük eşiğinin eczane ışığına göre kalibrasyonu (panelden ayarlanabilir)
- [ ] Tavan montaj aparatı (3D baskı) ve kablo kanalı
- [ ] KVKK aydınlatma metni ve kapı etiketi (taslak kılavuzda)
- [ ] Hub için HTTPS alan adı + yedekleme

## Faz 5 — Ürünleşme
- [ ] Eczaneye özel model: gerçek eczane görüntüleriyle (yukarıdan bakış) yeniden eğitilmiş, yalnızca
      kişi tanıyan YOLO11n → `yolo export format=imx` (Linux x86) + `imx500-package` ile .rpk
- [ ] Ticari lisans kararı: Ultralytics Enterprise (YOLO11n) ya da NanoDet (Apache-2.0)
- [ ] Yaş/cinsiyet modeli lisansı: MiVOLO v2 (model kartı Apache-2.0, eğitim verisi açıklanmamış) ya da
      Intel Open Model Zoo (Apache-2.0) / kendi modelimiz (FairFace + rızalı eczane görüntüleri)
- [ ] Sahada doğruluk denetimi: yüzlerce kapı geçişi elle sayılıp sistemle karşılaştırılır
- [ ] Çoklu kamera (büyük eczaneler) ve kameralar arası birleşik sayım
- [ ] Kampanya / raf değişikliği öncesi-sonrası karşılaştırma (A/B)
- [ ] Mobil bildirim: sıra 5 kişiyi geçince eczacıya uyarı
- [ ] Eczane yazılımı (satış verisi) ile entegrasyon → dönüşüm oranı
- [ ] OTA güncelleme ve cihaz filosu yönetimi

## Gizlilik ilkeleri (tasarım gereği)
- Kişiler yalnızca **anonim, geçici takip numarasıyla** izlenir; yüz tanıma ve kimlik tespiti yapılmaz.
- Yaş/cinsiyet tahmini açıksa görüntü yalnızca analiz edilene kadar bellekte durur; yüz şablonu saklanmaz,
  yalnızca ziyaret başına tahmin ve toplamlar tutulur. Varsayılan kapalıdır.
- Kamera görüntüsü diske **yazılmaz**. Isı haritası arka planı, içeride kimse yokken alınır.
- Canlı görüntü panelden kapatılabilir; açıkken kişiler bulanıklaştırılabilir.
- Saklanan veri: sayılar, süreler, bölge adları, ısı yoğunluk matrisi.
