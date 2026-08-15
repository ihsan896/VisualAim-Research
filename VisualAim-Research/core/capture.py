"""
core/capture.py
VisualAim-Research Screen Capture Module
Developer: İhsan
Version: 3.0 Professional

Description:
    High-performance screen capture optimized for Intel Iris Xe Graphics.
    Supports both DXGI (hardware) and MSS (software) capture methods.
    Implements frame buffering, FPS limiting, and multi-threading ready structure.

Hardware Compatibility:
    - Intel Iris Xe Graphics (Primary Target)
    - NVIDIA/AMD Discrete GPUs
    - 16GB+ RAM Recommended
"""

import cv2
import numpy as np
import ctypes
import sys
import time
import threading
from typing import Optional, Tuple, Callable
from dataclasses import dataclass
from enum import Enum
import warnings

warnings.filterwarnings('ignore')


def _install_comtypes_noise_filter() -> None:
    """
    dxcam + comtypes>=1.3 (Python 3.13'ün desteklediği tek seri) bilinen bir
    upstream hata yüzünden COM işaretçisi temizliğinde ölümcül olmayan
    'access violation' üretir ve Python bunu 'Exception ignored in __del__'
    olarak konsola basar. Yakalama çalışmaya devam eder; bu filtre YALNIZCA
    o spesifik gürültüyü susturur, diğer tüm hatalar aynen görünür.
    """
    default_hook = sys.unraisablehook

    def _hook(unraisable):
        obj = unraisable.object
        if (isinstance(unraisable.exc_value, OSError)
                and "access violation" in str(unraisable.exc_value)
                and getattr(obj, "__module__", "").startswith("comtypes")):
            return  # bilinen dxcam/comtypes temizlik hatası - yoksay
        default_hook(unraisable)

    sys.unraisablehook = _hook

class CaptureMethod(Enum):
    """Screen capture backend options"""
    AUTO = "auto"
    DXGI = "dxcam"
    MSS = "mss"
    PIL = "pil"

@dataclass
class FrameData:
    """Structured frame container with metadata"""
    image: np.ndarray
    timestamp: float
    fps: float
    frame_number: int
    capture_method: str

class ScreenCapture:
    """
    Professional screen capture class with hardware optimization.
    
    Features:
        - Automatic backend selection (DXGI > MSS > PIL)
        - Frame buffering for smooth processing
        - FPS limiting and monitoring
        - Thread-safe operations
        - Hardware-specific optimizations for Intel Iris Xe
        
    Args:
        capture_method: Backend selection (auto/dxcam/mss/pil)
        target_fps: Maximum capture FPS (0 = unlimited)
        buffer_size: Frame buffer queue size
    """
    
    def __init__(self,
                 capture_method: CaptureMethod = CaptureMethod.AUTO,
                 target_fps: int = 144,
                 buffer_size: int = 3,
                 region: Optional[Tuple[int, int, int, int]] = None):

        # Display metrics
        self.user32 = ctypes.windll.user32
        self.screen_width = self.user32.GetSystemMetrics(0)
        self.screen_height = self.user32.GetSystemMetrics(1)
        self.screen_center = (self.screen_width // 2, self.screen_height // 2)

        print(f"[ScreenCapture] Display detected: {self.screen_width}x{self.screen_height}")

        # Configuration
        self.capture_method_enum = capture_method
        self.target_fps = target_fps
        self.target_frame_time = 1.0 / target_fps if target_fps > 0 else 0
        self.buffer_size = buffer_size

        # Yakalama bölgesi (left, top, right, bottom) - None ise tam ekran.
        # Tüm ekranı işlemek yerine nişangâh çevresinden küçük bir pencere almak
        # işlem maliyetini onlarca kat düşürür (1920x1080 detect ~21ms,
        # 400x400 ~1.7ms). Koordinatlar için get_capture_offset() kullanılır.
        self._region: Optional[Tuple[int, int, int, int]] = None
        if region is not None:
            self.set_region(region)

        # Boş kare izleme (dxcam içerik değişmediğinde None döner)
        self._last_image: Optional[np.ndarray] = None
        self.empty_frames = 0            # toplam None dönüşü
        self.consecutive_errors = 0      # ardışık GERÇEK hata (istisna)
        self.reused_frames = 0           # None yerine son kare kullanıldı
        
        # Performance metrics
        self.frame_count = 0
        self.total_frames = 0
        self.current_fps = 0.0
        self.avg_fps = 0.0
        self.last_frame_time = time.perf_counter()
        self.start_time = time.perf_counter()
        
        # Son yakalanan kare (get_latest_frame ile okunur)
        self.latest_frame: Optional[FrameData] = None
        
        # Threading control
        self._running = False
        self._capture_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        
        # Initialize capture backend
        self._capture_backend = None
        self._backend_name = ""
        self._init_capture_backend()
        
        # Frame preprocessing options
        self.enable_resize = False
        self.target_resolution: Optional[Tuple[int, int]] = None
        
    def _init_capture_backend(self) -> None:
        """Initialize the best available capture backend"""
        
        method_priority = []
        
        if self.capture_method_enum == CaptureMethod.AUTO:
            # MSS öncelikli: her zaman kare döndürür ve donanım/sürücü
            # uyumsuzluğundan etkilenmez. DXCam daha hızlıdır ancak
            # config'te backend = dxcam yazılarak açıkça seçilmelidir.
            method_priority = [CaptureMethod.MSS, CaptureMethod.DXGI, CaptureMethod.PIL]
        else:
            method_priority = [self.capture_method_enum]
            
        for method in method_priority:
            try:
                if method == CaptureMethod.DXGI:
                    self._init_dxcam()
                    return
                elif method == CaptureMethod.MSS:
                    self._init_mss()
                    return
                elif method == CaptureMethod.PIL:
                    self._init_pil()
                    return
            except Exception as e:
                print(f"[ScreenCapture] {method.value} failed: {e}")
                continue
                
        raise RuntimeError("No capture backend available")
    
    def set_region(self, region: Optional[Tuple[int, int, int, int]]) -> None:
        """
        Yakalama bölgesini ayarla (ekran koordinatlarında left, top, right, bottom)

        None verilirse tam ekran yakalanır. Bölge ekran sınırlarına kırpılır;
        dxcam çift sayı olmayan genişlikte sorun çıkarabildiği için boyutlar
        çift sayıya yuvarlanır.
        """
        if region is None:
            self._region = None
            return

        left, top, right, bottom = (int(v) for v in region)
        left = max(0, min(left, self.screen_width - 2))
        top = max(0, min(top, self.screen_height - 2))
        right = max(left + 2, min(right, self.screen_width))
        bottom = max(top + 2, min(bottom, self.screen_height))

        # Genişlik/yükseklik çift olsun
        right -= (right - left) % 2
        bottom -= (bottom - top) % 2

        self._region = (left, top, right, bottom)
        self._last_image = None  # eski boyuttaki kare artık geçersiz
        print(f"[ScreenCapture] Capture region: {right-left}x{bottom-top} @ ({left},{top})")

    def set_center_region(self, width: int, height: int,
                          offset_x: int = 0, offset_y: int = 0) -> None:
        """Ekran merkezine göre bölge ayarla (nişangâh çevresi)"""
        cx = self.screen_center[0] + offset_x
        cy = self.screen_center[1] + offset_y
        self.set_region((cx - width // 2, cy - height // 2,
                         cx + width // 2, cy + height // 2))

    def get_capture_offset(self) -> Tuple[int, int]:
        """
        Yakalanan karenin sol üst köşesinin ekran koordinatı

        Kare içindeki (x, y) -> ekran koordinatı = offset + (x, y)
        """
        return (self._region[0], self._region[1]) if self._region else (0, 0)

    def get_local_center(self) -> Tuple[int, int]:
        """Nişangâhın (ekran merkezi) yakalanan kare içindeki koordinatı"""
        ox, oy = self.get_capture_offset()
        return (self.screen_center[0] - ox, self.screen_center[1] - oy)

    def _init_dxcam(self) -> None:
        """Initialize DXCam (DirectX) hardware acceleration"""
        try:
            _install_comtypes_noise_filter()
            import dxcam

            # output_color="BGR": her karede cv2.cvtColor çağrısını ortadan
            # kaldırır (1920x1080'de ~1.5ms, 2560x1440'ta ~2.7ms/kare kazanç)
            self._capture_backend = dxcam.create(output_color="BGR")
            if self._capture_backend is None:
                raise RuntimeError("dxcam.create() returned None")
            self._backend_name = "dxcam"
            print(f"[ScreenCapture] OK - DXCam initialized (Hardware accelerated, BGR)")

        except ImportError:
            raise ImportError("dxcam not installed")
            
    def _init_mss(self) -> None:
        """Initialize MSS (Multi-Screen Shot) software capture"""
        try:
            import mss
            
            self._capture_backend = mss.mss()
            self.monitor = self._capture_backend.monitors[1]  # Primary monitor
            self._backend_name = "mss"
            print(f"[ScreenCapture] OK - MSS initialized (Software mode)")
            
        except ImportError:
            raise ImportError("mss not installed")
            
    def _init_pil(self) -> None:
        """Initialize PIL (Pillow) fallback"""
        try:
            from PIL import ImageGrab

            self._capture_backend = ImageGrab
            self._backend_name = "pil"
            print(f"[ScreenCapture] OK - PIL initialized (Fallback mode)")

        except ImportError:
            raise ImportError("PIL/Pillow not installed")
    
    def grab(self) -> Optional[np.ndarray]:
        """
        Capture single frame with timing control.
        
        Returns:
            numpy.ndarray: BGR format image or None if failed
            
        Timing:
            Respects target_fps to prevent CPU overload
        """
        # FPS limiting
        if self.target_frame_time > 0:
            elapsed = time.perf_counter() - self.last_frame_time
            if elapsed < self.target_frame_time:
                time.sleep(self.target_frame_time - elapsed)
        
        frame = None

        try:
            if self._backend_name == "dxcam":
                # dxcam, ekran içeriği son grab'den beri DEĞİŞMEDİYSE None döner.
                # Bu bir hata değildir; ölçümde grab'lerin ~%57'si böyleydi.
                frame = (self._capture_backend.grab(region=self._region)
                         if self._region else self._capture_backend.grab())

            elif self._backend_name == "mss":
                screenshot = self._capture_backend.grab(self._mss_area())
                frame = cv2.cvtColor(np.array(screenshot), cv2.COLOR_BGRA2BGR)

            elif self._backend_name == "pil":
                bbox = self._region  # PIL bbox = (left, top, right, bottom)
                screenshot = self._capture_backend.grab(bbox=bbox)
                frame = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

            self.consecutive_errors = 0

        except Exception as e:
            self.consecutive_errors += 1
            print(f"[ScreenCapture] Capture error ({self.consecutive_errors}): {e}")
            # Backend gerçekten çöktüyse (ör. DXGI device removed) yazılım
            # yakalamaya geç; sessizce boş kare döndürmeye devam etme.
            if self.consecutive_errors >= 5 and self._backend_name != "mss":
                self._fallback_to_mss()
            return None

        if frame is None:
            # İçerik değişmemiş: son geçerli kareyi yeniden kullan.
            # Aksi halde ana döngü kareyi düşürüyor ve hedef kaybediliyordu.
            self.empty_frames += 1
            if self._last_image is not None:
                self.reused_frames += 1
                self._update_metrics()
                return self._last_image
            return None

        self._last_image = frame
        self._update_metrics()

        # Create frame data package
        frame_data = FrameData(
            image=frame,
            timestamp=time.perf_counter(),
            fps=self.current_fps,
            frame_number=self.total_frames,
            capture_method=self._backend_name
        )

        with self._lock:
            self.latest_frame = frame_data

        return frame

    def _mss_area(self) -> dict:
        """MSS için yakalama alanı sözlüğü (bölge veya tam ekran)"""
        if self._region:
            left, top, right, bottom = self._region
            return {"left": left, "top": top,
                    "width": right - left, "height": bottom - top}
        return self.monitor

    def _fallback_to_mss(self) -> None:
        """Donanım yakalama çöktüğünde yazılım yakalamaya geç"""
        print("[ScreenCapture] Backend failed repeatedly - switching to MSS")
        try:
            old = self._capture_backend
            self._backend_name = ""
            self._capture_backend = None
            try:
                if old is not None and hasattr(old, "release"):
                    old.release()
            except Exception:
                pass

            self._init_mss()
            self.consecutive_errors = 0
            self._last_image = None
        except Exception as e:
            print(f"[ScreenCapture] MSS fallback failed: {e}")

    def get_capture_stats(self) -> dict:
        """Yakalama sağlık bilgisi (boş kare oranı vb.)"""
        total = max(1, self.total_frames + self.empty_frames)
        return {
            "backend": self._backend_name,
            "total_frames": self.total_frames,
            "empty_frames": self.empty_frames,
            "reused_frames": self.reused_frames,
            "empty_ratio": self.empty_frames / total,
            "consecutive_errors": self.consecutive_errors,
            "region": self._region,
        }
    
    def _update_metrics(self) -> None:
        """Update FPS and timing metrics"""
        current_time = time.perf_counter()
        self.frame_count += 1
        self.total_frames += 1
        
        # Calculate instantaneous FPS
        delta = current_time - self.last_frame_time
        if delta > 0:
            instant_fps = 1.0 / delta
            # Smooth FPS calculation (EMA)
            self.current_fps = (self.current_fps * 0.9) + (instant_fps * 0.1)
        
        self.last_frame_time = current_time
        
        # Calculate average FPS over last second
        if current_time - self.start_time >= 1.0:
            self.avg_fps = self.frame_count / (current_time - self.start_time)
            self.frame_count = 0
            self.start_time = current_time
    
    def start_capture_thread(self, callback: Optional[Callable] = None) -> None:
        """
        Start background capture thread for async operation.
        
        Args:
            callback: Function to call with each new frame
        """
        if self._running:
            return
            
        self._running = True
        
        def capture_loop():
            while self._running:
                frame = self.grab()
                if frame is not None and callback:
                    try:
                        callback(frame)
                    except Exception as e:
                        print(f"[ScreenCapture] Callback error: {e}")
                        
        self._capture_thread = threading.Thread(target=capture_loop, daemon=True)
        self._capture_thread.start()
        print(f"[ScreenCapture] Capture thread started at {self.target_fps} FPS")
    
    def stop_capture_thread(self) -> None:
        """Stop background capture thread"""
        self._running = False
        if self._capture_thread:
            self._capture_thread.join(timeout=1.0)
            self._capture_thread = None
            print("[ScreenCapture] Capture thread stopped")
    
    def get_latest_frame(self) -> Optional[FrameData]:
        """Get the most recent captured frame"""
        with self._lock:
            return self.latest_frame
    
    def get_fps(self) -> float:
        """Get current FPS"""
        return self.current_fps
    
    def get_average_fps(self) -> float:
        """Get average FPS over time"""
        return self.avg_fps
    
    def get_screen_center(self) -> Tuple[int, int]:
        """Get screen center coordinates (crosshair position)"""
        return self.screen_center
    
    def get_dimensions(self) -> Tuple[int, int]:
        """Get screen dimensions"""
        return (self.screen_width, self.screen_height)
    
    def set_target_fps(self, fps: int) -> None:
        """Change target FPS dynamically"""
        self.target_fps = fps
        self.target_frame_time = 1.0 / fps if fps > 0 else 0
        print(f"[ScreenCapture] Target FPS changed to {fps}")
    
    def get_backend_name(self) -> str:
        """Get current capture backend name"""
        return self._backend_name
    
    def close(self) -> None:
        """Cleanup resources (birden fazla kez cagrilabilir - idempotent)"""
        # Ikinci cagride DXCam/COM isaretcisi zaten birakilmis olur; tekrar
        # release etmek cift-release riski demektir.
        if getattr(self, "_closed", False):
            return
        self._closed = True

        self.stop_capture_thread()

        try:
            if self._backend_name == "dxcam" and self._capture_backend:
                # DXCam + comtypes temizlik sırası önemli:
                # COM işaretçileri yorumlayıcı kapanışına kalırsa comtypes
                # CoUninitialize sonrası Release çağırır -> access violation.
                # Önce stop/release, sonra referansı bırak ve hemen topla.
                try:
                    if getattr(self._capture_backend, "is_capturing", False):
                        self._capture_backend.stop()
                except Exception:
                    pass
                try:
                    self._capture_backend.release()
                except Exception:
                    pass
                self._capture_backend = None
                import gc
                gc.collect()
            elif self._backend_name == "mss" and self._capture_backend:
                self._capture_backend.close()
        except Exception:
            pass  # Silent cleanup

        print("[ScreenCapture] Resources released")


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

    print("=" * 70)
    print("VisualAim-Research Screen Capture Test")
    print("Developer: İhsan")
    print("=" * 70)
    
    # Initialize with auto-detection
    capture = ScreenCapture(
        capture_method=CaptureMethod.AUTO,
        target_fps=60  # Intel Iris Xe için güvenli değer
    )
    
    print(f"\nBackend: {capture.get_backend_name()}")
    print(f"Screen: {capture.get_dimensions()}")
    print(f"Center: {capture.get_screen_center()}")
    print("\nTest starting... (Press Ctrl+C to stop)\n")
    
    try:
        test_duration = 10  # seconds
        start = time.perf_counter()
        
        while time.perf_counter() - start < test_duration:
            frame = capture.grab()
            
            if frame is not None:
                fps = capture.get_fps()
                avg_fps = capture.get_average_fps()
                
                status = f"FPS: {fps:.1f} | AVG: {avg_fps:.1f} | Frame: {capture.total_frames}"
                print(f"\r{status:60}", end="", flush=True)
                
                # Optional: Save test frame
                if capture.total_frames == 100:
                    cv2.imwrite("capture_test_frame.png", frame)
                    print("\n[Test] Sample frame saved: capture_test_frame.png")
            
            time.sleep(0.001)  # Small delay to prevent CPU overload
            
    except KeyboardInterrupt:
        print("\n\n[Test] Interrupted by user")
    finally:
        print("\n" + "=" * 70)
        print(f"Final Stats:")
        print(f"  Total Frames: {capture.total_frames}")
        print(f"  Average FPS: {capture.get_average_fps():.2f}")
        print(f"  Backend: {capture.get_backend_name()}")
        print("=" * 70)
        # close() cagrilmazsa DXCam COM isaretcileri yorumlayici kapanisina
        # kalir -> comtypes CoUninitialize sonrasi Release -> access violation.
        capture.close()