"""
core/input_controller.py
VisualAim-Research Input Control Module
Developer: İhsan
Version: 4.0 Professional

Description:
    Hardware-level mouse control using Windows API (SendInput).

    İki hareket modeli destekler:
      1) RELATIVE (oyun içi / raw-input): move_relative / move_smooth / move_bezier
         Fare deltalarını "mouse count" olarak enjekte eder. Oyunun kendi
         sensitivity'si devrede olduğu için piksel->count kalibrasyonu
         `sensitivity` çarpanı ile yapılır. (Valorant/CS gibi raw-input oyunlar
         Windows fare ivmesini yok sayar; kalibrasyon burada kilit önemdedir.)
      2) ABSOLUTE (masaüstü / normal pencere): move_absolute
         Ekran koordinatına birebir gider, Windows fare ivmesinden ETKİLENMEZ.

Features:
    - Direct hardware input via SendInput (doğru argtypes + ULONG_PTR)
    - SendInput dönüş değeri kontrolü (sessiz başarısızlık yok)
    - Exact-landing smooth / bezier hareket (adım toplamı = hedef delta)
    - Alt-piksel artık (remainder) taşıma -> uzun vadede sapma yok
    - Randomized delays & micro-jitter (insansı)
    - Thread-safe operations
"""

import ctypes
from ctypes import wintypes
import time
import random
import math
from typing import Tuple, Optional
from dataclasses import dataclass
from enum import Enum
import threading


# Pointer-genişliğinde imzasız tamsayı (ULONG_PTR). dwExtraInfo bunun tipinde
# olmalı; POINTER(ULONG) semantik olarak yanlıştır.
ULONG_PTR = ctypes.c_size_t


class MoveType(Enum):
    """Movement type selection"""
    INSTANT = "instant"      # Direct movement (fastest, least human)
    SMOOTH = "smooth"        # Eased interpolation
    BEZIER = "bezier"        # Quadratic Bezier curve (most human-like)
    HYBRID = "hybrid"        # Distance-based auto-selection


@dataclass
class MovementProfile:
    """Human-like movement parameters"""
    base_speed: float = 1.0
    speed_variation: float = 0.2
    min_steps: int = 3
    max_steps: int = 15
    micro_jitter: bool = True
    jitter_amount: int = 2


# --- Windows INPUT yapıları (modül düzeyinde bir kez tanımlanır) ---------------
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("mi", _MOUSEINPUT),
    ]


class InputController:
    """
    Professional hardware-level input controller

    Uses Windows SendInput API for direct hardware communication.

    Args:
        sensitivity: RELATIVE hareketlerde piksel -> mouse count çarpanı.
                     1.0 = 1 piksel isteği 1 count gönderir. Oyun içinde
                     nişanın hedefe tam oturması için bunu kalibre edin
                     (bkz. calibrate()).
    """

    # Windows API sabitleri
    INPUT_MOUSE = 0
    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_ABSOLUTE = 0x8000
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_WHEEL = 0x0800

    SM_CXSCREEN = 0
    SM_CYSCREEN = 1

    def __init__(self, sensitivity: float = 1.0):
        self.user32 = ctypes.windll.user32

        # SendInput / GetCursorPos imzalarını tanımla (64-bit doğruluğu)
        self.user32.SendInput.argtypes = [
            wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int
        ]
        self.user32.SendInput.restype = wintypes.UINT
        self.user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self.user32.GetCursorPos.restype = wintypes.BOOL

        # Ekran ölçüleri
        self.screen_width = self.user32.GetSystemMetrics(self.SM_CXSCREEN)
        self.screen_height = self.user32.GetSystemMetrics(self.SM_CYSCREEN)

        # Kalibrasyon
        self.sensitivity = sensitivity

        # Alt-piksel artık taşıyıcıları (relative)
        self._rem_x = 0.0
        self._rem_y = 0.0

        # İstatistik
        self.last_move_time = 0.0
        self.total_distance_moved = 0.0
        self.move_count = 0
        self.failed_sends = 0

        # Thread güvenliği
        self._lock = threading.RLock()

        # Varsayılan profil
        self.profile = MovementProfile()

        print(f"[InputController] Initialized (sensitivity={self.sensitivity})")
        print(f"[InputController] Screen: {self.screen_width}x{self.screen_height}")

    # ------------------------------------------------------------------ #
    # Düşük seviye gönderim
    # ------------------------------------------------------------------ #
    def _send(self, dx: int, dy: int, flags: int, mouse_data: int = 0) -> bool:
        """Tek bir mouse INPUT olayı gönderir. Başarı -> True."""
        inp = _INPUT()
        inp.type = self.INPUT_MOUSE
        inp.mi = _MOUSEINPUT(dx, dy, mouse_data & 0xFFFFFFFF, flags, 0, 0)
        sent = self.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
        if sent != 1:
            self.failed_sends += 1
            return False
        return True

    def _emit_relative(self, px_dx: float, px_dy: float) -> None:
        """
        Piksel cinsinden relative hareketi sensitivity ile count'a çevirip
        gönderir. Alt-piksel artıkları taşınır (uzun vadede sapma olmaz).
        """
        # Artık (remainder) okuma-değiştirme-yazma işlemi de kilit altında
        # olmalı; aksi halde iki thread aynı anda çağırdığında artık bozulur
        # ve hareket sapar. (_lock RLock, iç içe alınabilir.)
        with self._lock:
            self._rem_x += px_dx * self.sensitivity
            self._rem_y += px_dy * self.sensitivity

            # Sıfıra doğru kırp (truncate) ve artığı sakla
            step_x = math.trunc(self._rem_x)
            step_y = math.trunc(self._rem_y)
            self._rem_x -= step_x
            self._rem_y -= step_y

            if step_x == 0 and step_y == 0:
                return

            if self._send(step_x, step_y, self.MOUSEEVENTF_MOVE):
                self.last_move_time = time.perf_counter()
                self.total_distance_moved += math.hypot(step_x, step_y)
                self.move_count += 1

    # ------------------------------------------------------------------ #
    # RELATIVE hareket (oyun içi / raw-input)
    # ------------------------------------------------------------------ #
    def move_relative(self, dx: float, dy: float) -> None:
        """
        Anlık relative hareket (piksel). sensitivity ile count'a çevrilir.
        """
        if dx == 0 and dy == 0:
            return
        self._emit_relative(dx, dy)

    def move_smooth(self, dx: float, dy: float,
                    duration_ms: Optional[float] = None) -> None:
        """
        Yumuşak (eased) relative hareket. Adımların toplamı TAM olarak
        (dx, dy)'e eşittir; hedefe eksiksiz oturur.
        """
        if dx == 0 and dy == 0:
            return

        distance = math.hypot(dx, dy)

        if duration_ms is None:
            base_duration = (distance / 100) * 20  # ~20ms / 100px
            duration_ms = base_duration * random.uniform(0.8, 1.2)

        steps = int(max(self.profile.min_steps,
                        min(self.profile.max_steps, distance / 10)))
        steps = max(1, steps)
        step_delay = (duration_ms / 1000.0) / steps

        emitted_x = 0.0  # şimdiye kadar gönderilen toplam piksel (jitter dahil)
        emitted_y = 0.0

        for i in range(1, steps + 1):
            progress = i / steps
            eased = self._ease_out_quad(progress)

            # Bu adımda olması gereken kümülatif konum
            target_x = dx * eased
            target_y = dy * eased

            # Son adım değilse insansı mikro-jitter (son adımda YOK ki tam otursun)
            if i < steps and self.profile.micro_jitter and random.random() < 0.3:
                target_x += random.randint(-self.profile.jitter_amount,
                                           self.profile.jitter_amount)
                target_y += random.randint(-self.profile.jitter_amount,
                                           self.profile.jitter_amount)

            move_x = target_x - emitted_x
            move_y = target_y - emitted_y
            emitted_x += move_x
            emitted_y += move_y

            self._emit_relative(move_x, move_y)
            time.sleep(max(0.0, step_delay * random.uniform(0.9, 1.1)))

    def move_bezier(self, dx: float, dy: float,
                    control_point: Optional[Tuple[float, float]] = None) -> None:
        """
        Karesel Bezier eğrisiyle relative hareket (en insansı). Eğri, hedefe
        (dx, dy) tam olarak varır (B(1) = P2 = hedef).
        """
        if dx == 0 and dy == 0:
            return

        if control_point is None:
            control_point = (random.uniform(0.2, 0.8), random.uniform(0.1, 0.9))
        cpx, cpy = control_point

        steps = random.randint(8, 20)
        emitted_x = 0.0
        emitted_y = 0.0

        for i in range(1, steps + 1):
            t = i / steps
            inv = 1 - t
            # B(t) = 2(1-t)t * P1 + t^2 * P2  (P0 = 0)
            curve_x = (2 * inv * t * cpx * dx) + (t * t * dx)
            curve_y = (2 * inv * t * cpy * dy) + (t * t * dy)

            move_x = curve_x - emitted_x
            move_y = curve_y - emitted_y
            emitted_x += move_x
            emitted_y += move_y

            self._emit_relative(move_x, move_y)
            time.sleep(random.uniform(0.001, 0.003))

    def move_to_target(self, dx: float, dy: float,
                       move_type: MoveType = MoveType.SMOOTH) -> None:
        """Mesafe/tipe göre relative hareket seçimi."""
        distance = math.hypot(dx, dy)

        if move_type == MoveType.INSTANT:
            self.move_relative(dx, dy)
        elif move_type == MoveType.SMOOTH:
            self.move_smooth(dx, dy)
        elif move_type == MoveType.BEZIER:
            self.move_bezier(dx, dy)
        elif move_type == MoveType.HYBRID:
            if distance < 50:
                self.move_relative(dx, dy)
            elif distance < 200:
                self.move_smooth(dx, dy)
            else:
                self.move_bezier(dx, dy)

    # ------------------------------------------------------------------ #
    # ABSOLUTE hareket (masaüstü / normal pencere) - ivmeden etkilenmez
    # ------------------------------------------------------------------ #
    def move_absolute(self, x: int, y: int) -> bool:
        """
        Ekran koordinatına (x, y) birebir gider. Windows fare ivmesinden
        ETKİLENMEZ. Raw-input oyunlarda kamera için ÇALIŞMAZ (masaüstü için).
        """
        x = max(0, min(self.screen_width - 1, int(x)))
        y = max(0, min(self.screen_height - 1, int(y)))
        nx = int(x * 65535 / (self.screen_width - 1))
        ny = int(y * 65535 / (self.screen_height - 1))
        with self._lock:
            ok = self._send(nx, ny,
                            self.MOUSEEVENTF_MOVE | self.MOUSEEVENTF_ABSOLUTE)
            if ok:
                self.last_move_time = time.perf_counter()
                self.move_count += 1
            return ok

    # ------------------------------------------------------------------ #
    # Tıklama / tuş
    # ------------------------------------------------------------------ #
    def press_left(self) -> None:
        with self._lock:
            self._send(0, 0, self.MOUSEEVENTF_LEFTDOWN)

    def release_left(self) -> None:
        with self._lock:
            self._send(0, 0, self.MOUSEEVENTF_LEFTUP)

    def press_right(self) -> None:
        with self._lock:
            self._send(0, 0, self.MOUSEEVENTF_RIGHTDOWN)

    def release_right(self) -> None:
        with self._lock:
            self._send(0, 0, self.MOUSEEVENTF_RIGHTUP)

    def click_left(self, delay_ms: Optional[Tuple[int, int]] = None) -> None:
        """Sol tık; down/up arası rastgele (insansı) gecikme."""
        if delay_ms is None:
            delay_ms = (random.randint(5, 15), random.randint(20, 50))
        lo, hi = min(delay_ms), max(delay_ms)
        with self._lock:
            self._send(0, 0, self.MOUSEEVENTF_LEFTDOWN)
            time.sleep(random.randint(lo, hi) / 1000.0)
            self._send(0, 0, self.MOUSEEVENTF_LEFTUP)

    def click_right(self) -> None:
        with self._lock:
            self._send(0, 0, self.MOUSEEVENTF_RIGHTDOWN)
            time.sleep(random.uniform(0.01, 0.03))
            self._send(0, 0, self.MOUSEEVENTF_RIGHTUP)

    def scroll(self, amount: int) -> None:
        """Dikey scroll. amount > 0 yukarı, < 0 aşağı (120 = bir çentik)."""
        with self._lock:
            self._send(0, 0, self.MOUSEEVENTF_WHEEL, mouse_data=amount)

    # ------------------------------------------------------------------ #
    # Yardımcılar
    # ------------------------------------------------------------------ #
    def get_cursor_pos(self) -> Tuple[int, int]:
        """Anlık fare konumu (ekran koordinatı)."""
        pt = wintypes.POINT()
        self.user32.GetCursorPos(ctypes.byref(pt))
        return (pt.x, pt.y)

    def _ease_out_quad(self, t: float) -> float:
        return 1 - (1 - t) * (1 - t)

    def set_profile(self, profile: MovementProfile) -> None:
        self.profile = profile

    def set_sensitivity(self, sensitivity: float) -> None:
        """RELATIVE piksel->count çarpanını değiştirir."""
        self.sensitivity = sensitivity
        self._rem_x = 0.0
        self._rem_y = 0.0

    def calibrate(self, requested_px: int = 200) -> float:
        """
        Basit kalibrasyon yardımı (MASAÜSTÜ için; raw-input oyunda kamera
        döndüğü için imleç ölçümü geçerli olmaz).

        `requested_px` kadar relative hareket ettirir, imlecin gerçekte kaç
        piksel gittiğini ölçer ve düzeltilmiş sensitivity önerir.
        """
        start = self.get_cursor_pos()
        old_sens = self.sensitivity
        self._rem_x = self._rem_y = 0.0
        self.move_relative(requested_px, 0)
        time.sleep(0.05)
        end = self.get_cursor_pos()
        actual = end[0] - start[0]
        if actual == 0:
            print("[InputController] Kalibrasyon: hareket ölçülemedi.")
            return old_sens
        suggested = old_sens * (requested_px / actual)
        print(f"[InputController] Kalibrasyon: istenen={requested_px}px "
              f"gerçekleşen={actual}px -> önerilen sensitivity={suggested:.3f}")
        return suggested

    def get_stats(self) -> dict:
        return {
            "total_moves": self.move_count,
            "total_distance": int(self.total_distance_moved),
            "avg_move_distance": (self.total_distance_moved / self.move_count
                                  if self.move_count > 0 else 0),
            "failed_sends": self.failed_sends,
        }


# =============================================================================
# STANDALONE TEST
# =============================================================================

if __name__ == "__main__":
    # Windows konsolu cp1252/cp857 olabilir; Turkce karakterler (I, s, g) ve
    # sembol karakterleri UnicodeEncodeError firlatip testi komple cokertiyordu.
    # Kodlamayi degistirmeden sadece hata politikasini gevsetiyoruz:
    # desteklenmeyen karakter '?' olarak yazilir, program cokmez.
    import sys as _sys
    for _stream in (_sys.stdout, _sys.stderr):
        try:
            _stream.reconfigure(errors="replace")
        except Exception:
            pass

    print("=" * 60)
    print("Input Controller Test")
    print("Developer: İhsan")
    print("=" * 60)

    controller = InputController()

    print("\n[Test A] ABSOLUTE hareket doğruluğu (ivmeden bağımsız):")
    for tx, ty in [(400, 400), (1280, 720), (2000, 1000)]:
        controller.move_absolute(tx, ty)
        time.sleep(0.15)
        print(f"  hedef=({tx},{ty})  ->  gerçekleşen={controller.get_cursor_pos()}")

    print("\n[Test B] RELATIVE smooth/bezier (ekran ortasından):")
    controller.move_absolute(1280, 720); time.sleep(0.2)
    s = controller.get_cursor_pos()
    controller.move_smooth(150, 0); time.sleep(0.1)
    print(f"  smooth(+150,0):  {s} -> {controller.get_cursor_pos()}")
    s = controller.get_cursor_pos()
    controller.move_bezier(0, 150); time.sleep(0.1)
    print(f"  bezier(0,+150):  {s} -> {controller.get_cursor_pos()}")

    controller.move_absolute(1280, 720)
    print("\nStats:", controller.get_stats())
    print("=" * 60)
    print("Test tamamlandı")
    print("=" * 60)
