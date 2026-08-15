# VisualAim-Research

**Eğitim amaçlı aimbot tasarımı** — bilgisayarlı görü, gerçek zamanlı sistem tasarımı ve performans optimizasyonu üzerine uygulamalı bir çalışma.

Bu proje, ekran görüntüsü üzerinden renk tabanlı hedef tespiti yapan, Kalman filtresiyle hareket tahmini yürüten ve sonucu fare girdisine çeviren tam bir boru hattını (pipeline) uçtan uca gösterir. Amaç bir oyunda avantaj sağlamak değil; **gerçek zamanlı görüntü işleme sistemlerinin nasıl tasarlandığını, ölçüldüğünü ve optimize edildiğini** somut bir örnek üzerinden anlatmaktır.

---

## ⚠️ Önemli Uyarı

Bu yazılımı **çevrim içi rekabetçi oyunlarda kullanmayın.**

- Valorant (Riot Vanguard) ve CS2 (VAC) dahil tüm modern rekabetçi oyunların **kullanım koşullarını ihlal eder**. Tespit edilmesi **kalıcı hesap yasağı** ile sonuçlanır.
- Vanguard çekirdek seviyesinde (kernel-mode) çalışan bir hile önleme sistemidir. Bu proje onu atlatmayı hedeflemez, atlatamaz ve böyle bir amaç taşımaz.
- Diğer oyuncuların deneyimini bozmak, oyunun kendisine olduğu kadar onunla vakit geçiren insanlara da zarar verir.

**Sorumlu kullanım:** Kendi makinenizde, çevrim dışı ortamlarda çalıştırın — atış poligonu, çevrim dışı pratik modu, kendi kurduğunuz özel sunucu. Tespit ve takip katmanları sentetik kareler (düz renkli dikdörtgenler) üzerinde de çalışır; algoritmayı incelemek için oyuna hiç ihtiyaç yoktur.

Yazarı ve katkıda bulunanlar, yazılımın kötüye kullanımından doğacak sonuçlardan sorumlu değildir.

---

## Neyi öğretir?

Bu depo, "çalışan bir prototip" ile "gerçek zamanlı çalışan bir sistem" arasındaki farkı gösterir. Öne çıkan konular:

| Konu | Nerede |
|---|---|
| HSV renk uzayında eşikleme, morfoloji, kontur filtreleme | `core/detector.py` |
| Kalman filtresi (4 durumlu: konum + hız) ile hareket tahmini | `core/kalman_tracker.py` |
| Kapalı döngü kontrol: her karede hataya oranlı düzeltme | `core/aim_controller.py` |
| Windows `SendInput` ile alt-piksel doğrulukta göreli fare hareketi | `core/input_controller.py` |
| DXGI (dxcam) vs GDI (MSS) ekran yakalama — **130 kat fark** | `core/capture.py` |
| Şema tabanlı yapılandırma, tip dönüşümü, doğrulama | `ui/config_manager.py` |
| Thread'ler arası koordinasyon, bloklamayan tasarım | `main.py` |
| Ölçüm odaklı optimizasyon (profil çıkarmadan optimize etmeyin) | aşağıdaki tablolar |

---

## Nasıl çalışır?

```
┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│ 1. Yakalama  │──▶│ 2. Tespit    │──▶│ 3. Takip     │──▶│ 4. Kontrol   │
│              │   │              │   │              │   │              │
│ dxcam / MSS  │   │ BGR→HSV      │   │ Kalman       │   │ Hata × hız   │
│ bölge veya   │   │ inRange ×2   │   │ (x,y,vx,vy)  │   │ FOV kontrolü │
│ tam ekran    │   │ morfoloji    │   │ tahmin +     │   │ SendInput    │
│              │   │ kontur+filtre│   │ güven skoru  │   │ (göreli)     │
└──────────────┘   └──────────────┘   └──────────────┘   └──────────────┘
      2.5 ms            2.0 ms            <0.1 ms            <0.1 ms
```

**1. Yakalama** — Ekranın tamamı veya nişangâh çevresindeki bir bölge alınır. dxcam (DXGI Desktop Duplication) donanım hızlandırmalıdır; ekran içeriği değişmediğinde `None` döner, bu bir hata değildir — sistem son geçerli kareyi yeniden kullanır.

**2. Tespit** — Kare HSV'ye çevrilir, iki renk aralığı (kırmızı ton dairesini sardığı için) ile maskelenir, morfoloji ile gürültü temizlenir, konturlar alan / en-boy oranı / doluluk ölçütleriyle elenir. Kalan hedefler nişangâha uzaklığa göre sıralanır.

**3. Takip** — Kalman filtresi hedefin konumunu ve hızını tahmin eder. Hedef geçici olarak kaybolduğunda (duman, örtülme) tahmin devam eder, her karede güven skoru düşer.

**4. Kontrol** — Nişangâh ile hedef arasındaki hata vektörü hesaplanır, FOV yarıçapının dışındaysa yok sayılır, içindeyse `hata × aim_speed` kadar hareket uygulanır. **Yumuşatma buradan gelir**: her kare hatanın bir oranını kapattığı için hareket doğal olarak yavaşlayarak yaklaşır — ayrı bir easing katmanına gerek yoktur.

---

## Ölçümler

Tüm sayılar bu depodaki kodla, Intel Core i5-1135G7 + Iris Xe üzerinde ölçülmüştür.

### Ekran yakalama (1920×1080 tam ekran)

| Backend | Kare süresi | Tavan FPS |
|---|---:|---:|
| MSS (GDI) | 52.2 ms | 19 |
| **dxcam (DXGI)** | **0.4–2.5 ms** | **400+** |

GDI tabanlı yakalama her karede tüm masaüstünü bit-blit eder. DXGI, GPU'nun zaten oluşturduğu kareyi doğrudan okur.

### Tespit maliyeti (2560×1440 kare)

| `detection_scale` | Süre | Tavan FPS | Koordinat sapması |
|---|---:|---:|---:|
| 1.00 | 25.0 ms | 40 | — |
| 0.50 | 7.8 ms | 128 | 0 px |
| 0.33 | 3.5 ms | 289 | 1 px |
| **0.25** | **2.0 ms** | **503** | **0 px** |

Tespit maliyeti piksel sayısıyla doğru orantılıdır. Kare küçültülerek işlenir, bulunan koordinatlar tam çözünürlüğe geri ölçeklenir. Küçültmede `INTER_NEAREST` kullanılır: `INTER_AREA` komşu pikselleri ortalayıp hedef rengini arka planla harmanlıyor ve HSV eşiğinin dışına taşıyordu (ayrıca 10 kat daha pahalı).

### Oyun tespiti

| Yöntem | Süre |
|---|---:|
| `psutil.process_iter()` — tüm süreçleri tara | 37.4 ms |
| **Öndeki pencere → PID → exe adı** | **0.03 ms** |

Kare başına 37 ms kabul edilemez. Sistem iki katmanlı çalışır: exe adı saniyede 4 kez (ucuz yol), tam süreç taraması 2 saniyede bir (yalnızca "oyun hiç çalışıyor mu" sorusu için).

### Uçtan uca

```
yakalama : 2.52 ms
tespit   : 2.41 ms
─────────────────
TOPLAM   : 4.95 ms/kare  →  202 FPS
```

---

## Kurulum

**Gereksinimler:** Windows 10/11, Python 3.10+ (3.13 ile test edildi)

```bash
git clone https://github.com/<kullanici>/VisualAim-Research.git
cd VisualAim-Research
python -m pip install -r requirements.txt
```

| Paket | Neden |
|---|---|
| `opencv-python` | Görüntü işleme boru hattı |
| `numpy` | Dizi işlemleri |
| `dxcam` + `comtypes` | DXGI donanım hızlandırmalı yakalama |
| `mss` | Yazılımsal yakalama (yedek) |
| `pillow` | PIL yakalama (ikinci yedek) |
| `keyboard` | Global kısayol tuşları |
| `psutil` | Oyun süreci tespiti (opsiyonel — yoksa pencere başlığına düşer) |

---

## Kullanım

```bash
python main.py                # terminal menüsü ile
python main.py --no-menu      # yalnızca kısayol tuşları
```

### Kısayollar

| Tuş | İşlev |
|---|---|
| `F2` | Nişan yardımı aç/kapat |
| `F3` | Tetik aç/kapat |
| `F6` | Geri tepme telafisi aç/kapat |
| `F4` | Güvenli çıkış |

### Terminal menüsü

```
[1] START BOT          [6] RECOIL CONTROL
[2] STOP BOT           [7] DISPLAY / DEBUG
[3] PERFORMANCE        [8] LOAD PROFILE
[4] AIMBOT SETTINGS    [9] SAVE PROFILE
[5] TRIGGERBOT         [l] VIEW LOGS
                       [0] EXIT
```

Durum çubuğu canlı FPS, gecikme, aktif mod ve hedef durumunu gösterir. Menü ayrı bir thread'de çalışır; ana döngüyü bloklamaz.

---

## Renk kalibrasyonu

Sistemin tüm "zekâsı" hangi rengin hedef sayılacağı sorusundadır. Yanlış aralık = ya hiç tespit ya da her şeyin hedef sanılması.

```bash
# Ekranı yakalayıp PNG kaydet (analiz veya paylaşım için)
python calibrate_hsv.py --snapshot 8 --backend dxcam

# Pencere açmadan, global tuşlarla örnekle (tam ekran uygulamalar için)
python calibrate_hsv.py --live --apply --backend dxcam
#   F7 örnek al · F8 kaydet · F9 sıfırla · F10 çık

# Pencereli mod (fareyle tıklayarak)
python calibrate_hsv.py --apply
#   BOŞLUK dondur · sol tık örnekle · M maske · K kaydet · Q çık
```

Araç ton dairesinin sarmasını (kırmızı gibi 0 ve 179 civarına yayılan renkler) otomatik algılar ve gerektiğinde **iki ayrı aralık** üretir. Sonuç ekranında örneklerin tutarlılığı da raporlanır:

```
Ornek rengi : kırmızı (~2°)  (medyan H=1 S=228 V=241)
Tutarlilik  : H sapma=1.8  S sapma=12.4  V sapma=15.1
```

Yüksek sapma "farklı şeylere tıklandı" demektir. Aralık sınırları **yüzdelik (p10/p90)** ile hesaplanır — tek bir yanlış tıklama aralığı bozmasın diye.

---

## Yapılandırma

Tüm ayarlar `research_config.ini` içindedir. Şema tabanlıdır: her anahtarın tipi, sınırları ve varsayılanı `ui/config_manager.py` içinde tanımlıdır; aralık dışı değerler kırpılır, geçersiz seçenekler varsayılana döner.

### Öne çıkan ayarlar

```ini
[capture]
backend = dxcam            ; dxcam | mss | pil | auto
full_screen = true         ; false ise fov_x/fov_y bölgesi kullanılır
detection_scale = 0.25     ; tespit öncesi küçültme (tam ekranda kritik)
target_fps = 60

[color]
lower_h = 0                ; birincil HSV aralığı
upper_h = 10
lower_h2 = 170             ; ikincil aralık (ton dairesi sarması için)
upper_h2 = 179
min_blob_size = 40         ; gürültü elemesi
min_solidity = 0.5         ; parçalı şekilleri eler

[aim]
mode = smooth              ; smooth | snap | hybrid
speed = 0.35               ; her karede kapatılacak hata oranı
fov_radius = 250           ; bu yarıçapın dışındaki hedefler yok sayılır
head_offset = 0.28         ; kutunun üstünden aşağı oran (kafa hizası)

[game]
use_process_check = true   ; exe adıyla tespit (başlıktan güvenilir)
process_names = VALORANT.exe, cs2.exe
pause_when_closed = true   ; oyun kapalıyken bekleme moduna geç

[profile]
auto_apply = false         ; true ise performans profili elle ayarları ezer
```

`profile.auto_apply = false` yapmazsanız performans profili her açılışta `aim.speed`, `aim.smoothing`, `trigger.delay_*` ve `capture.target_fps` değerlerini kendi değerleriyle değiştirir.

### Oyun profilleri

`profiles/*.json` içinde oyuna özel ayar setleri bulunur (renk aralıkları, silah geri tepme paternleri, nişan tercihleri). Menüden `[8] LOAD PROFILE` ile yüklenir.

---

## Proje yapısı

```
VisualAim-Research/
├── core/
│   ├── capture.py          # Ekran yakalama (dxcam/MSS/PIL), bölge desteği
│   ├── detector.py         # HSV eşikleme, morfoloji, kontur filtreleme
│   ├── kalman_tracker.py   # 4 durumlu Kalman filtresi
│   ├── aim_controller.py   # FOV kontrolü, hareket hesabı
│   ├── input_controller.py # SendInput, alt-piksel biriktirme
│   ├── trigger.py          # Tetik mantığı, mod kontrolü
│   └── recoil.py           # Silaha özel geri tepme paternleri
├── modules/
│   ├── anti_ban.py         # İnsansı gecikme ve sapma üretimi
│   ├── hotkey_manager.py   # Global kısayol yönetimi
│   └── performance_profiles.py
├── ui/
│   ├── config_manager.py   # Şema tabanlı INI/JSON yapılandırma
│   ├── logger.py           # Renkli log + metrik toplama
│   └── menu.py             # Terminal arayüzü
├── profiles/               # Oyuna özel JSON profilleri
├── main.py                 # Ana döngü ve koordinasyon
├── calibrate_hsv.py        # Renk kalibrasyon aracı
└── research_config.ini     # Yapılandırma
```

Toplam ~9.400 satır Python.

---

## Tasarım notları

Geliştirme sürecinde çıkan, gerçek zamanlı sistemler için genellenebilir dersler:

**Bloklamayan olmak, hızlı olmaktan önce gelir.** Yakalama 2.5 ms'ye indirildikten sonra bile sistem 18 FPS'te takılıydı. Sebep: fare hareketi fonksiyonu içinde `time.sleep(50ms)` vardı ve hedef göründüğü sürece her kare bunu bekliyordu. Tek bir `sleep`, tüm optimizasyonu geçersiz kılabilir.

**Kapalı döngü zaten yumuşatır.** Her karede hatanın %35'ini kapatan bir kontrolcü, üstüne easing eklenmesine ihtiyaç duymaz; eklenen katman yalnızca gecikme üretir.

**"Boş kare" her zaman hata değildir.** DXGI, ekran içeriği değişmediğinde kare döndürmez. Bunu hata sayan kod kareleri düşürüyor ve hedefi kaybediyordu; doğru davranış son geçerli kareyi kullanmaktır.

**Ölçmeden optimize etmeyin.** Sistemin yavaş olmasının sebebi "Intel Iris Xe'nin zayıflığı" sanılıyordu. Ölçüm, sürenin %97'sinin ekran yakalama backend'inde harcandığını gösterdi — tek satırlık bir ayar değişikliği 6.9 FPS'i 202 FPS yaptı.

**Okunmayan ayar, ayar değildir.** Yapılandırma dosyasındaki onlarca anahtar hiçbir yerden okunmuyordu; kullanıcı değiştirdiğini sanıyor, sistem sabit değerlerle çalışıyordu. Ayrıca `configparser` satır içi yorumları varsayılan olarak değerin parçası sayar — `head_offset = 0.28  # kafa hizası` satırı sessizce varsayılana düşer.

---

## Test

Sentetik karelerle çalışan test paketleri; oyun veya gerçek fare hareketi gerektirmez.

```bash
python ui/config_manager.py        # yapılandırma katmanı öz-testi
python modules/performance_profiles.py
python test_valorant_profile.py    # profil dosyası doğrulaması
```

---

## Katkı ve lisans

Bu bir öğrenme projesidir. Hata bildirimleri ve iyileştirme önerileri açıktır; ancak **hile önleme sistemlerini atlatmaya yönelik katkılar kabul edilmez.**

Lisans belirtilmemiştir. Eklemeyi planlıyorsanız, bu tür projeler için genellikle kullanımı kısıtlayıcı bir lisans (veya "yalnızca eğitim amaçlı" ibaresi) tercih edilir.

---

## Kaynaklar

- [OpenCV — Renk uzayları ve eşikleme](https://docs.opencv.org/4.x/df/d9d/tutorial_py_colorspaces.html)
- [OpenCV — Morfolojik dönüşümler](https://docs.opencv.org/4.x/d9/d61/tutorial_py_morphological_ops.html)
- [Kalman filtresi — görsel anlatım](https://www.bzarg.com/p/how-a-kalman-filter-works-in-pictures/)
- [DXGI Desktop Duplication API](https://learn.microsoft.com/en-us/windows/win32/direct3ddxgi/desktop-dup-api)
- [Windows SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)
