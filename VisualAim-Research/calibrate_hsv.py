#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VisualAim-Research: HSV Kalibrasyon Aracı
=========================================
Tespit renk aralıklarını oyun ekranından ölçerek belirler.

İKİ MOD:

  --live  (ÖNERİLEN, tam ekran oyunlar için)
      Pencere açılmaz. Fareyi oyunda hedefin üzerine getirip global tuşlarla
      örnek alırsınız; oyundan çıkmanıza gerek yoktur.

          python calibrate_hsv.py --live --apply

          F7  : farenin altındaki renkten örnek al
          F8  : sonucu kaydet (--apply / --profile ile)
          F9  : örnekleri sıfırla
          F10 : çık

  (varsayılan) pencereli mod
      Ekran görüntüsünü bir pencerede gösterir, fareyle tıklayarak örnek
      alırsınız. DİKKAT: OpenCV tuşları YALNIZCA pencere odaktayken alır -
      önce pencereye bir kez tıklamanız gerekir.

          python calibrate_hsv.py --apply

          BOŞLUK : kareyi dondur / çözdür
          Sol tık: o noktadan renk örneği al
          U      : son örneği geri al
          R      : tüm örnekleri sıfırla
          M      : maske önizlemesini aç/kapa
          K      : sonucu kaydet
          Q/ESC  : çık (pencerenin X'i de çalışır)

Diğer seçenekler:
    --full            tüm ekranı yakala (varsayılan: merkezden 800x800)
    --size 1200       bölge boyutu
    --backend dxcam   yakalama backend'i (auto/mss/dxcam/pil)
    --profile cs2     sonucu profiles/cs2.json'a yaz

Yazar: İhsan
"""

import sys
import time
import argparse
from ctypes import wintypes
from pathlib import Path
from typing import List, Tuple, Optional

# Windows konsol kodlaması (Türkçe karakterler için)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import cv2
    import numpy as np
except ImportError as e:
    print(f"[FATAL] OpenCV/numpy gerekli: {e}")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from core.capture import ScreenCapture, CaptureMethod
    from ui.config_manager import ConfigManager
except ImportError as e:
    print(f"[FATAL] Proje modülleri yüklenemedi: {e}")
    sys.exit(1)


WINDOW = "VisualAim - HSV Kalibrasyon"

# Tıklanan noktanın çevresinden alınacak kare pencere (piksel yarıçapı).
# Tek piksel gürültüye çok duyarlı; küçük bir alanın ortalaması daha kararlı.
SAMPLE_RADIUS = 3


class HSVCalibrator:
    """Ekrandan HSV örneği toplayıp aralık öneren kalibrasyon aracı"""

    def __init__(self, region_size: int = 800, full_screen: bool = False,
                 backend: str = "auto"):
        try:
            method = CaptureMethod(backend.lower())
        except ValueError:
            method = CaptureMethod.AUTO

        self.capture = ScreenCapture(capture_method=method, target_fps=30)

        if not full_screen:
            self.capture.set_center_region(region_size, region_size)

        self.samples: List[Tuple[int, int, int]] = []   # (H, S, V)
        self.frozen_frame: Optional[np.ndarray] = None
        self.is_frozen = False
        self.show_mask = False
        self.last_click: Optional[Tuple[int, int]] = None

    # ---------------------------------------------------------------- örnekleme
    def _on_mouse(self, event, x, y, flags, param):
        if event != cv2.EVENT_LBUTTONDOWN:
            return

        frame = self.frozen_frame if self.frozen_frame is not None else param
        if frame is None:
            return

        h, w = frame.shape[:2]
        if not (0 <= x < w and 0 <= y < h):
            return

        # Tıklanan noktanın çevresindeki küçük pencereden medyan al
        x0, x1 = max(0, x - SAMPLE_RADIUS), min(w, x + SAMPLE_RADIUS + 1)
        y0, y1 = max(0, y - SAMPLE_RADIUS), min(h, y + SAMPLE_RADIUS + 1)
        patch = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)

        hsv = tuple(int(v) for v in np.median(patch.reshape(-1, 3), axis=0))
        self.samples.append(hsv)
        self.last_click = (x, y)

        print(f"[Örnek {len(self.samples):2d}] H={hsv[0]:3d} S={hsv[1]:3d} V={hsv[2]:3d}"
              f"  (konum {x},{y})")

    # ------------------------------------------------------------------ analiz
    def suggest_range(self) -> Optional[dict]:
        """
        Toplanan örneklerden HSV aralığı öner

        Hue dairesel olduğu için (0 ve 179 komşudur) kırmızı gibi tonlarda
        basit min/max yanlış sonuç verir; sarma durumu ayrıca ele alınır.

        Returns:
            {"lower": (h,s,v), "upper": (h,s,v), "wrap": bool, "lower2":..., "upper2":...}
        """
        if not self.samples:
            return None

        arr = np.array(self.samples, dtype=np.int32)
        hues = np.sort(arr[:, 0])
        sats, vals = arr[:, 1], arr[:, 2]

        # Hue'da en büyük boşluğu bul: sarma varsa boşluk dizinin ortasındadır
        gaps = np.diff(hues)
        wrap_gap = (hues[0] + 180) - hues[-1]   # son -> ilk (daire üzerinden)
        wraps = len(hues) > 1 and gaps.size > 0 and gaps.max() > wrap_gap and gaps.max() > 30

        # Doygunluk/parlaklık sınırları YÜZDELİK ile hesaplanır.
        # min/max kullanmak tek bir yanlış tıklamayı aralığa dahil ediyordu:
        # 23 örnekten biri (S=17) alt sınırı 0'a çekip aralığı "her şey"
        # haline getirebiliyordu. p10/p90 bu tür aykırı değerleri dışlar.
        if len(arr) >= 5:
            s_lo_raw, s_hi_raw = np.percentile(sats, [10, 90])
            v_lo_raw, v_hi_raw = np.percentile(vals, [10, 90])
        else:
            s_lo_raw, s_hi_raw = sats.min(), sats.max()
            v_lo_raw, v_hi_raw = vals.min(), vals.max()

        s_lo = max(0, int(s_lo_raw) - 30)
        s_hi = min(255, int(s_hi_raw) + 30)
        v_lo = max(0, int(v_lo_raw) - 30)
        v_hi = min(255, int(v_hi_raw) + 30)

        h_pad = 5

        if not wraps:
            if len(arr) >= 5:
                h_lo_raw, h_hi_raw = np.percentile(hues, [10, 90])
            else:
                h_lo_raw, h_hi_raw = hues.min(), hues.max()
            h_lo = max(0, int(h_lo_raw) - h_pad)
            h_hi = min(179, int(h_hi_raw) + h_pad)
            return {
                "lower": (h_lo, s_lo, v_lo),
                "upper": (h_hi, s_hi, v_hi),
                "wrap": False,
                "spread": self._spread(arr),
            }

        # Sarma var: örnekleri iki kümeye ayır (ör. 0-10 ve 170-179)
        split_at = int(np.argmax(gaps))
        low_cluster = hues[:split_at + 1]     # 0'a yakın tonlar
        high_cluster = hues[split_at + 1:]    # 179'a yakın tonlar

        return {
            "lower": (max(0, int(low_cluster.min()) - h_pad), s_lo, v_lo),
            "upper": (min(179, int(low_cluster.max()) + h_pad), s_hi, v_hi),
            "lower2": (max(0, int(high_cluster.min()) - h_pad), s_lo, v_lo),
            "upper2": (min(179, int(high_cluster.max()) + h_pad), s_hi, v_hi),
            "wrap": True,
            "spread": self._spread(arr),
        }

    @staticmethod
    def _spread(arr: np.ndarray) -> dict:
        """
        Örneklerin ne kadar tutarlı olduğunu ölçer

        Yüksek standart sapma "farklı şeylere tıklandı" demektir; kullanıcı
        bunu göremezse tutarsız örneklerden üretilmiş kullanışsız bir aralığı
        farkında olmadan kaydediyor.
        """
        return {
            "h_std": float(arr[:, 0].std()),
            "s_std": float(arr[:, 1].std()),
            "v_std": float(arr[:, 2].std()),
            "h_median": float(np.median(arr[:, 0])),
            "s_median": float(np.median(arr[:, 1])),
            "v_median": float(np.median(arr[:, 2])),
        }

    @staticmethod
    def describe_hue(h: float) -> str:
        """Ton değerinin hangi renge denk geldiği (OpenCV H: 0-179)"""
        deg = h * 2
        names = [
            (15, "kırmızı"), (45, "turuncu"), (70, "sarı"), (150, "yeşil"),
            (200, "camgöbeği"), (260, "mavi"), (290, "mor"), (330, "pembe"), (360, "kırmızı"),
        ]
        for limit, name in names:
            if deg < limit:
                return f"{name} (~{deg:.0f}°)"
        return f"kırmızı (~{deg:.0f}°)"

    def build_mask(self, frame: np.ndarray, rng: dict) -> np.ndarray:
        """Önerilen aralıkla maske üret (önizleme için)"""
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array(rng["lower"]), np.array(rng["upper"]))
        if rng.get("wrap"):
            mask2 = cv2.inRange(hsv, np.array(rng["lower2"]), np.array(rng["upper2"]))
            mask = cv2.bitwise_or(mask, mask2)
        return mask

    # ------------------------------------------------------------------- çizim
    def _draw_overlay(self, frame: np.ndarray, rng: Optional[dict]) -> np.ndarray:
        view = frame.copy()
        h, w = view.shape[:2]

        # Nişangâh
        cv2.drawMarker(view, (w // 2, h // 2), (0, 255, 255),
                       cv2.MARKER_CROSS, 20, 1)

        if self.last_click:
            cv2.circle(view, self.last_click, 6, (0, 255, 0), 2)

        lines = [
            f"Ornek: {len(self.samples)}  |  {'DONDURULDU' if self.is_frozen else 'CANLI'}",
            "BOSLUK dondur | Sol tik ornek al | U geri al | R sifirla | M maske | K kaydet | Q cikis",
        ]
        if rng:
            lines.append(f"lower={rng['lower']}  upper={rng['upper']}")
            if rng.get("wrap"):
                lines.append(f"lower2={rng['lower2']}  upper2={rng['upper2']}  (kirmizi/sarmali ton)")
        else:
            lines.append("Hedef rengin uzerine tiklayin")

        # Okunur arka plan
        box_h = 20 * len(lines) + 10
        cv2.rectangle(view, (0, 0), (w, box_h), (0, 0, 0), -1)
        for i, text in enumerate(lines):
            cv2.putText(view, text, (8, 20 + i * 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1, cv2.LINE_AA)

        return view

    # -------------------------------------------------------------------- kayıt
    def save_to_config(self, rng: dict, config_path: str = "research_config.ini") -> bool:
        """Önerilen aralığı research_config.ini içine yaz"""
        try:
            config = ConfigManager(config_path=config_path, auto_create=False)

            lo, hi = rng["lower"], rng["upper"]
            lo2, hi2 = rng.get("lower2", lo), rng.get("upper2", hi)

            for key, value in [
                ("color.lower_h", lo[0]), ("color.lower_s", lo[1]), ("color.lower_v", lo[2]),
                ("color.upper_h", hi[0]), ("color.upper_s", hi[1]), ("color.upper_v", hi[2]),
                ("color.lower_h2", lo2[0]), ("color.lower_s2", lo2[1]), ("color.lower_v2", lo2[2]),
                ("color.upper_h2", hi2[0]), ("color.upper_s2", hi2[1]), ("color.upper_v2", hi2[2]),
            ]:
                config.set(key, int(value), save=False)

            config.save()
            print(f"\n[OK] Aralik kaydedildi: {config_path}")
            return True
        except Exception as e:
            print(f"\n[HATA] Config yazilamadi: {e}")
            return False

    def save_to_profile(self, rng: dict, profile_name: str,
                        profiles_dir: str = "profiles") -> bool:
        """Önerilen aralığı bir oyun profilinin color_detection.ranges alanına yaz"""
        import json

        path = Path(profiles_dir) / f"{profile_name}.json"
        if not path.exists():
            print(f"\n[HATA] Profil bulunamadi: {path}")
            return False

        try:
            data = json.loads(path.read_text(encoding="utf-8"))

            ranges = [{"name": "calibrated_1",
                       "lower": list(rng["lower"]), "upper": list(rng["upper"])}]
            if rng.get("wrap"):
                ranges.append({"name": "calibrated_2",
                               "lower": list(rng["lower2"]), "upper": list(rng["upper2"])})

            data.setdefault("color_detection", {})["ranges"] = ranges
            path.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")

            print(f"\n[OK] Profil guncellendi: {path}")
            return True
        except Exception as e:
            print(f"\n[HATA] Profil yazilamadi: {e}")
            return False

    # ---------------------------------------------------------- anlık görüntü
    def save_snapshot(self, delay: float = 5.0, out_dir: str = "logs") -> Optional[Path]:
        """
        Geri sayımdan sonra bir kare yakalayıp PNG olarak kaydet

        Print Screen yerine bunu kullanmak önemli: kare, tespit hattının
        gördüğü YOLLA (aynı backend, aynı renk düzeni) alınır. Böylece
        görüntüden ölçülen HSV değerleri ile çalışma anındaki değerler
        birebir aynı olur.

        Args:
            delay: Yakalamadan önce beklenecek saniye (oyuna geçmek için)
            out_dir: Çıktı dizini

        Returns:
            Kaydedilen dosyanın yolu (başarısızsa None)
        """
        self.capture.set_region(None)  # tam ekran

        print("=" * 70)
        print("ANLIK GORUNTU MODU")
        print("=" * 70)
        print(f"Backend: {self.capture.get_backend_name()}")
        print()
        print("SIMDI OYUNA GECIN ve dusman gorunur olsun.")
        print()

        for remaining in range(int(delay), 0, -1):
            print(f"  {remaining}...", flush=True)
            time.sleep(1.0)

        frame = self.capture.grab()
        if frame is None:
            print("\n[HATA] Kare alinamadi - baska backend deneyin (--backend mss)")
            return None

        path = Path(out_dir)
        path.mkdir(parents=True, exist_ok=True)
        out = path / time.strftime("snapshot_%Y%m%d_%H%M%S.png")
        cv2.imwrite(str(out), frame)

        h, w = frame.shape[:2]
        print(f"\n[OK] Kaydedildi: {out.resolve()}")
        print(f"     Boyut: {w}x{h}  |  Backend: {self.capture.get_backend_name()}")
        print()
        print("Bu dosyayi paylasarak renk araligini olcturebilirsiniz.")
        return out

    # -------------------------------------------------------------- canlı mod
    def run_live(self, apply_config: bool = False, profile: Optional[str] = None) -> None:
        """
        Pencere açmadan, global tuşlarla örnek alma modu

        Tam ekran oyunlarda pencereli mod kullanışsız: OpenCV tuşları yalnızca
        kendi penceresi odaktayken alır, oyundan çıkmak da gerekir. Bu mod
        GetAsyncKeyState ile tuşları oyundan çıkmadan okur, fare nerede ise
        oranın rengini örnekler.
        """
        import ctypes

        user32 = ctypes.windll.user32
        VK = {"sample": 0x76, "save": 0x77, "reset": 0x78, "quit": 0x79}  # F7..F10

        # Ekranın tamamını görmemiz gerekiyor - fare her yerde olabilir
        self.capture.set_region(None)

        print("=" * 70)
        print("HSV KALIBRASYON - CANLI MOD (pencere yok)")
        print("=" * 70)
        print(f"Backend: {self.capture.get_backend_name()}")
        print()
        print("  F7  : farenin altindaki renkten ornek al")
        print("  F8  : kaydet" + ("" if (apply_config or profile) else "  (--apply veya --profile gerekli)"))
        print("  F9  : ornekleri sifirla")
        print("  F10 : cikis")
        print()
        print("Oyuna gecin, fareyi DUSMANIN uzerine getirip F7'ye basin.")
        print("Farkli mesafelerden/isiktan 5-10 ornek alin.\n")

        pressed = {k: False for k in VK}

        def just_pressed(name: str) -> bool:
            """Tusa YENI basildi mi (basili tutmayi tekrar sayma)"""
            down = bool(user32.GetAsyncKeyState(VK[name]) & 0x8000)
            fired = down and not pressed[name]
            pressed[name] = down
            return fired

        try:
            while True:
                if just_pressed("quit"):
                    print("\n[Kalibrasyon] F10 - cikiliyor")
                    break

                if just_pressed("reset"):
                    self.samples.clear()
                    print("[Sifirla] Tum ornekler silindi")

                if just_pressed("sample"):
                    point = wintypes.POINT()
                    user32.GetCursorPos(ctypes.byref(point))
                    frame = self.capture.grab()

                    if frame is None:
                        print("[HATA] Kare alinamadi - baska bir backend deneyin (--backend mss)")
                    else:
                        h, w = frame.shape[:2]
                        x, y = point.x, point.y
                        if not (0 <= x < w and 0 <= y < h):
                            print(f"[HATA] Fare yakalama alani disinda ({x},{y}) / {w}x{h}")
                        else:
                            x0, x1 = max(0, x - SAMPLE_RADIUS), min(w, x + SAMPLE_RADIUS + 1)
                            y0, y1 = max(0, y - SAMPLE_RADIUS), min(h, y + SAMPLE_RADIUS + 1)
                            patch = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
                            hsv = tuple(int(v) for v in np.median(patch.reshape(-1, 3), axis=0))
                            self.samples.append(hsv)

                            rng = self.suggest_range()
                            print(f"[Ornek {len(self.samples):2d}] konum ({x},{y})  "
                                  f"H={hsv[0]:3d} S={hsv[1]:3d} V={hsv[2]:3d}   "
                                  f"-> onerilen: {rng['lower']} .. {rng['upper']}"
                                  + ("  (+ikinci aralik)" if rng.get("wrap") else ""))

                if just_pressed("save"):
                    rng = self.suggest_range()
                    if not rng:
                        print("[Kaydet] Once F7 ile ornek alin")
                    elif profile:
                        self.save_to_profile(rng, profile)
                    elif apply_config:
                        self.save_to_config(rng)
                    else:
                        print("[Kaydet] --apply veya --profile <ad> ile calistirin")

                time.sleep(0.03)

        except KeyboardInterrupt:
            print("\n[Kalibrasyon] Ctrl+C ile durduruldu")
        finally:
            self.capture.close()
            self._print_summary()

    # ------------------------------------------------------------------- döngü
    def run(self, apply_config: bool = False, profile: Optional[str] = None) -> None:
        print("=" * 70)
        print("HSV KALIBRASYON - PENCERELI MOD")
        print("=" * 70)
        print(f"Backend : {self.capture.get_backend_name()}")
        print(f"Bolge   : {self.capture.get_capture_stats()['region'] or 'tam ekran'}")
        print()
        print("!!! TUSLAR YALNIZCA PENCERE ODAKTAYKEN CALISIR !!!")
        print("Once acilan '%s' penceresine BIR KEZ TIKLAYIN." % WINDOW)
        print("Cikis: Q veya ESC (pencerenin X dugmesi de calisir)")
        print("Tam ekran oyunda pencereye tiklamak zorsa --live modunu kullanin.")
        print()

        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
        # Pencere birden fazla monitorde gorunmez yerde acilmasin
        cv2.moveWindow(WINDOW, 60, 60)
        try:
            cv2.setWindowProperty(WINDOW, cv2.WND_PROP_TOPMOST, 1)
        except Exception:
            pass

        live_frame = None
        try:
            while True:
                # Pencere X ile kapatildiysa dongu bitmeli (aksi halde
                # program terminalde asili kaliyor ve Ctrl+C gerekiyordu)
                try:
                    if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                        print("[Kalibrasyon] Pencere kapatildi")
                        break
                except cv2.error:
                    break

                if not self.is_frozen:
                    grabbed = self.capture.grab()
                    if grabbed is not None:
                        live_frame = grabbed

                frame = self.frozen_frame if self.is_frozen else live_frame
                if frame is None:
                    # Bos pencere odaklanamiyor ve tus almiyor; en azindan
                    # bilgilendirici bir goruntu goster ki cikis tuslari calissin
                    placeholder = np.zeros((200, 640, 3), dtype=np.uint8)
                    cv2.putText(placeholder, "Kare bekleniyor...", (20, 90),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
                    cv2.putText(placeholder, "Cikis: Q / ESC", (20, 130),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)
                    cv2.imshow(WINDOW, placeholder)
                    if cv2.waitKey(30) & 0xFF in (ord('q'), 27):
                        break
                    continue

                cv2.setMouseCallback(WINDOW, self._on_mouse, frame)

                rng = self.suggest_range()

                if self.show_mask and rng:
                    mask = self.build_mask(frame, rng)
                    view = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
                    view = self._draw_overlay(view, rng)
                else:
                    view = self._draw_overlay(frame, rng)

                cv2.imshow(WINDOW, view)

                key = cv2.waitKey(30) & 0xFF

                if key in (ord('q'), 27):
                    break
                elif key == ord(' '):
                    self.is_frozen = not self.is_frozen
                    self.frozen_frame = live_frame.copy() if self.is_frozen and live_frame is not None else None
                    print(f"[Kare] {'donduruldu' if self.is_frozen else 'cozuldu'}")
                elif key == ord('u') and self.samples:
                    removed = self.samples.pop()
                    print(f"[Geri al] H={removed[0]} S={removed[1]} V={removed[2]}")
                elif key == ord('r'):
                    self.samples.clear()
                    self.last_click = None
                    print("[Sifirla] Tum ornekler silindi")
                elif key == ord('m'):
                    self.show_mask = not self.show_mask
                elif key == ord('k'):
                    if not rng:
                        print("[Kaydet] Once ornek almalisin")
                    elif profile:
                        self.save_to_profile(rng, profile)
                    elif apply_config:
                        self.save_to_config(rng)
                    else:
                        print("[Kaydet] --apply veya --profile <ad> ile calistir")

        except KeyboardInterrupt:
            print("\n[Kalibrasyon] Kullanici tarafindan durduruldu")
        finally:
            cv2.destroyAllWindows()
            self.capture.close()
            self._print_summary()

    def _print_quality(self, rng: dict) -> None:
        """Örneklerin tutarlılığını ve aralığın kullanılabilirliğini değerlendir"""
        spread = rng.get("spread")
        if not spread:
            return

        print()
        print(f"  Ornek rengi : {self.describe_hue(spread['h_median'])}  "
              f"(medyan H={spread['h_median']:.0f} S={spread['s_median']:.0f} V={spread['v_median']:.0f})")
        print(f"  Tutarlilik  : H sapma={spread['h_std']:.1f}  "
              f"S sapma={spread['s_std']:.1f}  V sapma={spread['v_std']:.1f}")

        uyarilar = []
        if spread["h_std"] > 8:
            uyarilar.append("Ton sapmasi yuksek - farkli renklere tiklanmis olabilir")
        if spread["s_std"] > 60 or spread["v_std"] > 70:
            uyarilar.append("Doygunluk/parlaklik cok dagilmis - ayni nesnenin farkli "
                            "isikli bolgeleri veya farkli nesneler ornekleniyor")

        lo, hi = rng["lower"], rng["upper"]
        if lo[1] < 40:
            uyarilar.append(f"Doygunluk alt siniri cok dusuk ({lo[1]}) - gri/soluk "
                            "her sey hedef sayilir")
        if lo[2] < 40:
            uyarilar.append(f"Parlaklik alt siniri cok dusuk ({lo[2]}) - koyu bolgeler "
                            "hedef sayilir")
        if (hi[0] - lo[0]) > 40:
            uyarilar.append(f"Ton araligi cok genis ({lo[0]}-{hi[0]}) - birden fazla renk kapsaniyor")

        if uyarilar:
            print()
            print("  DIKKAT:")
            for u in uyarilar:
                print(f"    - {u}")
            print("    Ipucu: sadece DUSMAN konturuna, farkli mesafelerden 5-10 kez tiklayin.")

    def _print_summary(self) -> None:
        rng = self.suggest_range()
        print("\n" + "=" * 70)
        if not rng:
            print("Ornek alinmadi.")
            print("=" * 70)
            return

        print(f"SONUC ({len(self.samples)} ornek)")
        print("=" * 70)
        print(f"  lower  = {rng['lower']}")
        print(f"  upper  = {rng['upper']}")
        if rng.get("wrap"):
            print(f"  lower2 = {rng['lower2']}")
            print(f"  upper2 = {rng['upper2']}")
            print("  NOT: Ton dairesi sariyor (kirmizi gibi) - iki aralik kullanilmali")

        self._print_quality(rng)

        print("\nresearch_config.ini [color] bolumu icin:")
        lo, hi = rng["lower"], rng["upper"]
        lo2, hi2 = rng.get("lower2", lo), rng.get("upper2", hi)
        for name, val in [("lower_h", lo[0]), ("lower_s", lo[1]), ("lower_v", lo[2]),
                          ("upper_h", hi[0]), ("upper_s", hi[1]), ("upper_v", hi[2]),
                          ("lower_h2", lo2[0]), ("lower_s2", lo2[1]), ("lower_v2", lo2[2]),
                          ("upper_h2", hi2[0]), ("upper_s2", hi2[1]), ("upper_v2", hi2[2])]:
            print(f"  {name} = {val}")
        print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="VisualAim HSV kalibrasyon araci")
    parser.add_argument("--size", type=int, default=800,
                        help="Merkez bolge boyutu (varsayilan 800)")
    parser.add_argument("--full", action="store_true", help="Tum ekrani yakala")
    parser.add_argument("--backend", default="auto", choices=["auto", "mss", "dxcam", "pil"],
                        help="Yakalama backend'i")
    parser.add_argument("--apply", action="store_true",
                        help="K tusuna basildiginda research_config.ini'ye yaz")
    parser.add_argument("--profile", help="Kaydederken bu profile yaz (ör. valorant)")
    parser.add_argument("--live", action="store_true",
                        help="Pencere acmadan, global tuslarla ornek al (tam ekran oyunlar icin)")
    parser.add_argument("--snapshot", type=float, nargs="?", const=5.0, metavar="SANIYE",
                        help="N saniye geri sayimdan sonra bir kare yakalayip PNG kaydet "
                             "(varsayilan 5). Analiz icin paylasilabilir.")
    args = parser.parse_args()

    calibrator = HSVCalibrator(region_size=args.size,
                               full_screen=args.full or args.live or args.snapshot is not None,
                               backend=args.backend)
    if args.snapshot is not None:
        calibrator.save_snapshot(delay=args.snapshot)
        calibrator.capture.close()
    elif args.live:
        calibrator.run_live(apply_config=args.apply, profile=args.profile)
    else:
        calibrator.run(apply_config=args.apply, profile=args.profile)


if __name__ == "__main__":
    main()
