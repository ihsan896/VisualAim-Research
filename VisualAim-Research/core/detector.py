"""
core/detector.py
VisualAim-Research Target Detection Module
Developer: İhsan
Version: 3.0 Professional

Description:
    Advanced color-based target detection using HSV color space.
    Optimized for Valorant enemy highlights (red).
    Includes contour filtering, confidence scoring, and multi-target tracking.

Features:
    - Dual-range HSV for red color detection (circular hue)
    - Morphological operations for noise reduction
    - Aspect ratio filtering (human shape detection)
    - Distance-based target prioritization
    - Confidence scoring system
"""

import cv2
import numpy as np
from typing import List, Tuple, Optional
from dataclasses import dataclass, field
from enum import Enum
import time


class DetectionMode(Enum):
    """Detection sensitivity modes"""
    LEGIT = "legit"      # Conservative, low false positives
    BALANCED = "balanced" # Default setting
    AGGRESSIVE = "aggressive" # High sensitivity, may detect more


@dataclass
class Target:
    """
    Detected target container with full metrics
    
    Attributes:
        x, y: Top-left corner
        width, height: Bounding box dimensions
        center_x, center_y: Center point
        head_x, head_y: Aim point (head level)
        confidence: Detection confidence (0.0 - 1.0)
        aspect_ratio: Height/width ratio
        area: Contour area in pixels
        distance_from_center: Distance from screen center
        timestamp: Detection time
    """
    x: int
    y: int
    width: int
    height: int
    center_x: int
    center_y: int
    confidence: float
    aspect_ratio: float
    area: int
    # Alan olarak tanimli olmali: _process_contours disinda olusturulan her
    # Target'ta (test/mock/baska modul) t.distance_from_center AttributeError
    # firlatiyordu - targets.sort(key=...) de dahil.
    distance_from_center: float = 0.0
    timestamp: float = field(default_factory=time.time)
    
    # Calculated properties
    @property
    def head_x(self) -> int:
        """Head X coordinate (same as center)"""
        return self.center_x
    
    @property
    def head_y(self) -> int:
        """
        Head Y coordinate - optimized for Valorant
        
        Valorant head position is approximately 25-30% from top of hitbox
        """
        return int(self.y + (self.height * 0.28))
    
    @property
    def body_center_y(self) -> int:
        """Body center Y coordinate"""
        return self.center_y
    
    def distance_to(self, point_x: int, point_y: int) -> float:
        """Calculate Euclidean distance to a point"""
        return ((self.center_x - point_x) ** 2 + (self.center_y - point_y) ** 2) ** 0.5


class ColorDetector:
    """
    Professional color-based target detection system
    
    Optimized for Intel Iris Xe Graphics with efficient OpenCV operations
    """
    
    # Valorant enemy highlight colors (HSV)
    # Red appears at both ends of HSV hue spectrum (0-10 and 170-180)
    DEFAULT_LOWER_RED1 = np.array([0, 140, 140])
    DEFAULT_UPPER_RED1 = np.array([10, 255, 255])
    DEFAULT_LOWER_RED2 = np.array([170, 140, 140])
    DEFAULT_UPPER_RED2 = np.array([180, 255, 255])
    
    def __init__(self,
                 lower_red1: Optional[np.ndarray] = None,
                 upper_red1: Optional[np.ndarray] = None,
                 lower_red2: Optional[np.ndarray] = None,
                 upper_red2: Optional[np.ndarray] = None,
                 min_area: int = 80,
                 max_area: int = 10000,
                 min_aspect: float = 0.25,
                 max_aspect: float = 4.0,
                 confidence_threshold: float = 0.3,
                 mode: DetectionMode = DetectionMode.BALANCED,
                 min_solidity: float = 0.0,
                 blur_kernel: Tuple[int, int] = (3, 3),
                 morph_open_kernel: int = 3,
                 morph_close_kernel: int = 5,
                 morph_iterations: int = 1):
        """
        Args:
            min_solidity: Konturun dışbükey zarfına doluluk oranı alt sınırı.
                Delikli/parçalı lekeleri eler (0.0 = kapalı).
            blur_kernel: Maske yumuşatma çekirdeği (tek sayı olmalı)
            morph_open_kernel: Gürültü temizleme çekirdek boyutu
            morph_close_kernel: Delik doldurma çekirdek boyutu
            morph_iterations: Morfoloji tekrar sayısı

        Bu beş parametre research_config.ini [color] bölümünde tanımlıydı ama
        okunmuyordu; çekirdekler 3x3/5x5 olarak koda gömülüydü.
        """
        # Color ranges
        self.lower_red1 = lower_red1 if lower_red1 is not None else self.DEFAULT_LOWER_RED1
        self.upper_red1 = upper_red1 if upper_red1 is not None else self.DEFAULT_UPPER_RED1
        self.lower_red2 = lower_red2 if lower_red2 is not None else self.DEFAULT_LOWER_RED2
        self.upper_red2 = upper_red2 if upper_red2 is not None else self.DEFAULT_UPPER_RED2

        # Filtering parameters
        self.min_area = min_area
        self.max_area = max_area
        self.min_aspect = min_aspect
        self.max_aspect = max_aspect
        self.confidence_threshold = confidence_threshold
        self.min_solidity = max(0.0, min(1.0, min_solidity))
        self.mode = mode

        # Morphological kernels (config'ten; tek sayıya yuvarlanır)
        self.morph_iterations = max(1, int(morph_iterations))
        self.kernel_small = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, self._odd_size(morph_open_kernel))
        self.kernel_medium = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, self._odd_size(morph_close_kernel))
        self.blur_kernel = self._odd_pair(blur_kernel)

        # Performance metrics
        self.detection_count = 0
        self.total_processed = 0
        self.last_process_time = 0
        
        print(f"[ColorDetector] Initialized in {mode.value} mode")
        print(f"[ColorDetector] Area range: {min_area}-{max_area}px")
    
    def detect(self, frame: np.ndarray,
               screen_center: Optional[Tuple[int, int]] = None,
               scale: float = 1.0) -> Tuple[List[Target], np.ndarray]:
        """
        Main detection pipeline

        Args:
            frame: Input BGR image
            screen_center: (x, y) for distance calculation (tam çözünürlük koordinatı)
            scale: Tespit öncesi küçültme oranı (1.0 = küçültme yok).
                İşlem maliyeti piksel sayısıyla doğru orantılı olduğundan tam
                ekran taramasında belirleyicidir: 2560x1440 karede detect()
                ~38 ms sürerken scale=0.25 ile ~2.5 ms'ye iner. Bulunan hedef
                koordinatları tam çözünürlüğe geri ölçeklenir.

        Returns:
            targets: List of detected targets (sorted by distance)
            mask: Binary mask for debugging (küçültülmüş boyutta olabilir)
        """
        start_time = time.perf_counter()
        self.total_processed += 1

        # Get frame dimensions
        if frame is None or frame.size == 0:
            return [], np.array([])

        height, width = frame.shape[:2]
        if screen_center is None:
            screen_center = (width // 2, height // 2)

        # Küçültme (isteğe bağlı).
        # INTER_NEAREST seçildi: (1) INTER_AREA 2560x1440'ta tek başına ~3.5 ms
        # yerken bu ~0.3 ms, (2) komşu pikselleri ortalamadığı için HSV değerleri
        # BOZULMAZ - ortalama alan yöntem hedef rengini arka planla harmanlayıp
        # eşiğin dışına taşıyabiliyor.
        # NOT: ince kenarlık (outline) tabanlı oyunlarda düşük ölçek hedefi
        # kaçırabilir; böyle bir durumda detection_scale'i 0.5 veya 1.0 yapın.
        if 0.0 < scale < 1.0:
            frame = cv2.resize(frame, None, fx=scale, fy=scale,
                               interpolation=cv2.INTER_NEAREST)
        else:
            scale = 1.0

        # Step 1: Convert to HSV color space
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        
        # Step 2: Create color masks (red is circular in HSV)
        mask1 = cv2.inRange(hsv, self.lower_red1, self.upper_red1)
        mask2 = cv2.inRange(hsv, self.lower_red2, self.upper_red2)
        mask = cv2.bitwise_or(mask1, mask2)
        
        # Step 3: Noise reduction (morphological operations)
        mask = self._apply_morphology(mask)
        
        # Step 4: Find contours
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        # Step 5: Filter and create targets
        targets = self._process_contours(contours, screen_center, scale)
        
        # Update metrics
        self.last_process_time = time.perf_counter() - start_time
        
        return targets, mask
    
    @staticmethod
    def _odd_size(value: int) -> Tuple[int, int]:
        """Çekirdek boyutunu geçerli (tek, >=1) kare boyuta çevir"""
        size = max(1, int(value))
        if size % 2 == 0:
            size += 1
        return (size, size)

    @classmethod
    def _odd_pair(cls, pair) -> Tuple[int, int]:
        """(x, y) çekirdeğini tek sayılara yuvarla - GaussianBlur şartı"""
        try:
            kx, ky = int(pair[0]), int(pair[1])
        except (TypeError, IndexError, ValueError):
            kx = ky = 3
        kx = max(1, kx + (kx + 1) % 2)
        ky = max(1, ky + (ky + 1) % 2)
        return (kx, ky)

    def _apply_morphology(self, mask: np.ndarray) -> np.ndarray:
        """Apply morphological operations to clean up mask (config'ten ayarlı)"""
        # Opening: Remove small noise
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self.kernel_small,
                                iterations=self.morph_iterations)

        # Closing: Fill small holes
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self.kernel_medium,
                                iterations=self.morph_iterations)

        # Optional: Gaussian blur for smoother edges (1x1 = kapalı)
        if self.blur_kernel != (1, 1):
            mask = cv2.GaussianBlur(mask, self.blur_kernel, 0)

        return mask
    
    def _process_contours(self, contours: List,
                         screen_center: Tuple[int, int],
                         scale: float = 1.0) -> List[Target]:
        """
        Filter contours and create target objects

        Args:
            scale: detect() içinde uygulanan küçültme oranı. Alan eşikleri
                scale² ile küçülür (aksi halde küçültülmüş karede hiçbir
                kontur min_area'yı geçemez), sonuç koordinatları 1/scale ile
                tam çözünürlüğe geri ölçeklenir.
        """
        targets = []
        inv = 1.0 / scale if scale > 0 else 1.0
        area_factor = scale * scale
        min_area = self.min_area * area_factor

        # Üst sınıra pay: morfoloji ve blur konturun çevresine sabit ~1-2 px
        # ekler; bu, küçültülmüş karede alanı ORANTISAL olarak çok daha fazla
        # büyütür (10x25 blob -> 12x27 = %30 artış). Pay verilmezse hedef
        # max_area'yı aşıp eleniyordu. Alt sınır etkilenmez.
        max_area = self.max_area * area_factor * (1.35 if scale < 1.0 else 1.0)

        for cnt in contours:
            # Area filtering
            area = cv2.contourArea(cnt)
            if not (min_area <= area <= max_area):
                continue

            # Get bounding box
            x, y, w, h = cv2.boundingRect(cnt)
            
            # Aspect ratio filtering (human shape check)
            aspect = h / float(w) if w > 0 else 0
            if not (self.min_aspect <= aspect <= self.max_aspect):
                continue
            
            # Calculate center using moments (more accurate than bbox center)
            M = cv2.moments(cnt)
            if M["m00"] > 0:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])
            else:
                cx, cy = x + w // 2, y + h // 2
            
            # Calculate confidence based on contour solidity
            hull_area = cv2.contourArea(cv2.convexHull(cnt))
            solidity = area / hull_area if hull_area > 0 else 0

            # Doluluk filtresi (config: color.min_solidity) - parçalı/delikli
            # lekeleri eler. 0.0 verilirse devre dışı.
            if self.min_solidity > 0.0 and solidity < self.min_solidity:
                continue

            # Additional confidence from aspect ratio (closer to human = higher)
            human_like_score = 1.0 - abs(aspect - 1.8) / 1.8  # Peak at 1.8:1
            human_like_score = max(0, min(1, human_like_score))
            
            confidence = (solidity * 0.6) + (human_like_score * 0.4)
            
            if confidence < self.confidence_threshold:
                continue
            
            # Koordinatları tam çözünürlüğe geri ölçekle (scale=1.0 ise değişmez)
            if inv != 1.0:
                x, y = int(x * inv), int(y * inv)
                w, h = int(w * inv), int(h * inv)
                cx, cy = int(cx * inv), int(cy * inv)
                area = area * inv * inv

            # Calculate distance from screen center (tam çözünürlük koordinatında)
            distance = ((cx - screen_center[0]) ** 2 +
                       (cy - screen_center[1]) ** 2) ** 0.5

            target = Target(
                x=x, y=y, width=w, height=h,
                center_x=cx, center_y=cy,
                confidence=confidence,
                aspect_ratio=aspect,
                area=int(area),           # contourArea float doner, alan int
                distance_from_center=distance
            )

            targets.append(target)
            self.detection_count += 1
        
        # Sort by distance from center (closest first)
        targets.sort(key=lambda t: t.distance_from_center)
        
        return targets
    
    def set_color_range(self, lower: np.ndarray, upper: np.ndarray, 
                       range_id: int = 1) -> None:
        """Dynamically adjust color detection range"""
        if range_id == 1:
            self.lower_red1 = lower
            self.upper_red1 = upper
        else:
            self.lower_red2 = lower
            self.upper_red2 = upper
    
    def get_stats(self) -> dict:
        """Get detection statistics"""
        return {
            "total_processed": self.total_processed,
            "total_detections": self.detection_count,
            "avg_process_time_ms": self.last_process_time * 1000,
            "detection_rate": (self.detection_count / self.total_processed * 100) 
                             if self.total_processed > 0 else 0
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

    import cv2

    try:
        from .capture import ScreenCapture, CaptureMethod
    except ImportError:
        from capture import ScreenCapture, CaptureMethod

    print("=" * 70)
    print("VisualAim-Research Target Detection Test")
    print("Developer: İhsan")
    print("=" * 70)
    
    # Initialize capture
    cap = ScreenCapture(capture_method=CaptureMethod.AUTO, target_fps=60)
    detector = ColorDetector(mode=DetectionMode.BALANCED)
    
    screen_center = cap.get_screen_center()
    print(f"\nScreen Center: {screen_center}")
    print("\n[Test] Ekranda kırmızı bir şey göster (YouTube/Paint)")
    print("[Test] Çıkmak için 'Q' tuşuna bas\n")

    # Önizleme boyutu: tam ekran yakalama + tam boyut önizleme = sonsuz ayna
    # tüneli (pencere kendi görüntüsünü yakalar). Küçük sabit boyutlu pencere
    # bu geri beslemeyi zararsız hale getirir ve CPU/GPU yükünü düşürür.
    PREVIEW_WIDTH = 800

    cv2.namedWindow("Target Detection", cv2.WINDOW_NORMAL)
    cv2.namedWindow("Mask", cv2.WINDOW_NORMAL)
    cv2.moveWindow("Target Detection", 20, 20)
    cv2.moveWindow("Mask", 20 + PREVIEW_WIDTH + 40, 20)

    try:
        while True:
            frame = cap.grab()
            if frame is None:
                # Bos 'continue' = CPU'yu bosuna yakan sikisik dongu + OpenCV
                # olay kuyrugu beslenmedigi icin donan pencere.
                if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
                    break
                if cv2.getWindowProperty("Target Detection", cv2.WND_PROP_VISIBLE) < 1:
                    break
                continue

            # Detect targets
            targets, mask = detector.detect(frame, screen_center)
            
            # Visualize
            display = frame.copy()
            
            if targets:
                # Draw first target (closest to center)
                t = targets[0]
                
                # Bounding box
                cv2.rectangle(display, (t.x, t.y), 
                            (t.x + t.width, t.y + t.height), 
                            (0, 255, 0), 2)
                
                # Head point (aim here) - magenta: kırmızı çizersek dedektör
                # önizleme penceresindeki noktayı hedef sanıp geri besleme yapar
                cv2.circle(display, (t.head_x, t.head_y), 5, (255, 0, 255), -1)
                
                # Center point
                cv2.circle(display, (t.center_x, t.center_y), 3, (255, 0, 0), -1)
                
                # Info text
                info = f"Conf: {t.confidence:.2f} | Dist: {int(t.distance_from_center)}"
                cv2.putText(display, info, (t.x, t.y - 10),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                
                # Console output
                print(f"\rHedef: x={t.x}, y={t.y}, Güven={t.confidence:.2f}, "
                      f"Mesafe={int(t.distance_from_center)}px    ", end="")
            
            # Show FPS
            fps_text = f"FPS: {cap.get_fps():.1f}"
            cv2.putText(display, fps_text, (10, 30),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            
            # Display (küçültülmüş önizleme - ayna tüneli ve yük önlemi)
            scale = PREVIEW_WIDTH / display.shape[1]
            preview_size = (PREVIEW_WIDTH, int(display.shape[0] * scale))
            display_small = cv2.resize(display, preview_size,
                                       interpolation=cv2.INTER_AREA)
            mask_small = cv2.resize(mask, preview_size,
                                    interpolation=cv2.INTER_NEAREST)
            cv2.resizeWindow("Target Detection", *preview_size)
            cv2.resizeWindow("Mask", *preview_size)
            cv2.imshow("Target Detection", display_small)
            cv2.imshow("Mask", mask_small)
            
            if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
                break
            # Pencere X ile kapatildiysa dur; yoksa imshow her karede yenisini
            # acar (pencere yagmuru).
            if cv2.getWindowProperty("Target Detection", cv2.WND_PROP_VISIBLE) < 1:
                break

    except KeyboardInterrupt:
        print("\n[Test] Interrupted")
    finally:
        cv2.destroyAllWindows()
        for _ in range(4):      # Windows'ta pencerenin gercekten kapanmasi icin
            cv2.waitKey(1)
        cap.close()
        print("\n" + "=" * 70)
        stats = detector.get_stats()
        print(f"Total frames: {stats['total_processed']}")
        print(f"Total detections: {stats['total_detections']}")
        print(f"Detection rate: {stats['detection_rate']:.1f}%")
        print(f"Avg process time: {stats['avg_process_time_ms']:.2f}ms")
        print("=" * 70)
        
        cv2.destroyAllWindows()
        cap.close()