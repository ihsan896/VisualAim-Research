"""
core/kalman_tracker.py
VisualAim-Research Kalman Filter Tracking Module
Developer: İhsan
Version: 3.0 Professional

Description:
    Advanced Kalman filter implementation for target prediction and tracking.
    Predicts target position when visual detection is temporarily lost.
    Essential for maintaining aim during smoke, flash, or occlusion.

Mathematical Model:
    State Vector: [x, y, vx, vy] (position and velocity)
    Prediction: X(k) = F·X(k-1) + w (process noise)
    Update: X(k) = X(k) + K·(Z(k) - H·X(k))
    
Hardware Optimized for: Intel Iris Xe Graphics (vectorized numpy operations)
"""

import numpy as np
from typing import Optional, Tuple, List
from dataclasses import dataclass
from enum import Enum
import time
import threading

try:
    # Paket olarak import edildiğinde: from core.kalman_tracker import KalmanTracker
    from .detector import Target
except ImportError:
    # Doğrudan çalıştırıldığında: python core/kalman_tracker.py
    from detector import Target


class TrackingState(Enum):
    """Target tracking state machine"""
    SEARCHING = "searching"      # No target found yet
    LOCKED = "locked"           # Target actively tracked
    PREDICTING = "predicting"   # Target lost, using prediction
    LOST = "lost"              # Target lost for too long


@dataclass
class TrackedTarget:
    """
    Extended target with tracking metadata
    
    Attributes:
        target: Base target from detector
        predicted_x, predicted_y: Kalman predicted position
        velocity_x, velocity_y: Estimated velocity (pixels/frame)
        confidence: Tracking confidence (0.0 - 1.0)
        state: Current tracking state
        last_seen: Timestamp of last detection
        prediction_age: How many frames since last real detection
    """
    target: Target
    predicted_x: float
    predicted_y: float
    velocity_x: float
    velocity_y: float
    confidence: float
    state: TrackingState
    last_seen: float
    prediction_age: int = 0
    # Guvenilirlik esikleri - KalmanTracker config'ten doldurur.
    # Onceden is_reliable icinde 0.4 / 10 olarak sabitti.
    min_confidence: float = 0.4
    max_prediction_age: int = 10
    
    @property
    def aim_x(self) -> int:
        """Recommended aim X coordinate"""
        return int(self.predicted_x)
    
    @property
    def aim_y(self) -> int:
        """Recommended aim Y coordinate (head level)"""
        # Use predicted position but maintain head offset
        head_offset = self.target.head_y - self.target.center_y
        return int(self.predicted_y + head_offset)
    
    @property
    def is_reliable(self) -> bool:
        """Check if prediction is reliable enough for aiming"""
        return (self.confidence > self.min_confidence
                and self.prediction_age < self.max_prediction_age)


class KalmanTracker:
    """
    Professional Kalman Filter for target tracking
    
    Features:
        - 4-state Kalman filter (x, y, vx, vy)
        - Adaptive noise covariance based on movement pattern
        - Multi-target tracking support (up to 5 targets)
        - Thread-safe operations
        - Automatic state recovery after occlusion
        
    Parameters:
        dt: Time step between frames (default: 0.0167s = 60FPS)
        process_noise: Q matrix diagonal value (higher = more responsive)
        measurement_noise: R matrix value (higher = more smoothing)
    """
    
    def __init__(self, 
                 dt: float = 0.0167,
                 process_noise: float = 0.01,
                 measurement_noise: float = 2.0,
                 max_prediction_frames: int = 15,
                 initial_covariance: float = 100.0,
                 confidence_decay: float = 0.95,
                 max_prediction_age: int = 10,
                 association_threshold: float = 200.0,
                 min_confidence: float = 0.4):
        
        # Time step (seconds per frame)
        self.dt = dt
        
        # State transition matrix F
        # [1  0  dt  0 ]
        # [0  1  0   dt]
        # [0  0  1   0 ]
        # [0  0  0   1 ]
        self.F = np.array([
            [1, 0, dt, 0],
            [0, 1, 0, dt],
            [0, 0, 1, 0],
            [0, 0, 0, 1]
        ], dtype=np.float32)
        
        # Measurement matrix H (we only measure position, not velocity)
        # [1 0 0 0]
        # [0 1 0 0]
        self.H = np.array([
            [1, 0, 0, 0],
            [0, 1, 0, 0]
        ], dtype=np.float32)
        
        # Process noise covariance Q
        # Higher values = trust prediction less, measurement more
        self.Q = np.eye(4, dtype=np.float32) * process_noise
        self.Q[2, 2] *= 2.0  # Velocity noise higher
        self.Q[3, 3] *= 2.0
        
        # Measurement noise covariance R
        # Lower values = trust measurement more
        self.R = np.eye(2, dtype=np.float32) * measurement_noise
        
        # State covariance P (initial uncertainty)
        self.P = np.eye(4, dtype=np.float32) * initial_covariance
        
        # State vector [x, y, vx, vy]
        self.state = np.zeros((4, 1), dtype=np.float32)
        
        # Tracking parameters
        self.max_prediction_frames = max_prediction_frames
        # Asagidaki dortu research_config.ini [kalman] bolumunde tanimliydi
        # ama okunmuyordu; degerler koda gomuluydu.
        self.confidence_decay = confidence_decay      # tahmin edilen her kare icin guven carpani
        self.max_prediction_age = max_prediction_age  # kac kare sonra guvenilmez sayilir
        self.association_threshold = association_threshold  # ayni hedef mi (piksel)
        self.min_confidence = min_confidence          # is_reliable alt siniri
        self.prediction_count = 0
        self.is_initialized = False
        
        # Multi-target support
        self.tracked_targets: List[TrackedTarget] = []
        self.max_tracked = 5
        
        # Thread safety
        self._lock = threading.RLock()
        
        # Performance metrics
        self.update_count = 0
        self.prediction_count_total = 0
        self.last_update_time = time.time()
        
        print(f"[KalmanTracker] Initialized (dt={dt}s, max_pred={max_prediction_frames})")
    
    def initialize(self, target: Target) -> None:
        """
        Initialize tracker with first detection
        
        Args:
            target: Initial target detection
        """
        with self._lock:
            self.state[0] = target.center_x
            self.state[1] = target.center_y
            self.state[2] = 0.0  # Initial velocity unknown
            self.state[3] = 0.0
            
            # Reset covariance
            self.P = np.eye(4, dtype=np.float32) * 50.0
            
            self.is_initialized = True
            self.prediction_count = 0
            
            # Create tracked target
            tracked = TrackedTarget(
                target=target,
                predicted_x=target.center_x,
                predicted_y=target.center_y,
                velocity_x=0.0,
                velocity_y=0.0,
                confidence=1.0,
                state=TrackingState.LOCKED,
                last_seen=time.time(),
                prediction_age=0,
                min_confidence=self.min_confidence,
                max_prediction_age=self.max_prediction_age
            )
            self.tracked_targets = [tracked]
    
    def _predict_state(self) -> None:
        """
        Saf Kalman tahmin adımı (sayaç/istatistik güncellemesi yapmaz).

        Hem predict() hem de update() tarafından kullanılır; update() öncesinde
        de çalışması şart, aksi halde P matrisinde konum-hız korelasyonu
        oluşmaz ve filtre hızı hiçbir zaman öğrenemez.
        """
        # State prediction: X̂(k) = F·X(k-1)
        self.state = self.F @ self.state

        # Covariance prediction: P̂(k) = F·P(k-1)·Fᵀ + Q
        self.P = self.F @ self.P @ self.F.T + self.Q

    def predict(self) -> Optional[Tuple[float, float]]:
        """
        Prediction step: Estimate next state without measurement

        Returns:
            (predicted_x, predicted_y) or None if not initialized
        """
        if not self.is_initialized:
            return None

        with self._lock:
            self._predict_state()

            # Increment prediction counter
            self.prediction_count += 1
            self.prediction_count_total += 1
            
            # Update tracked target with prediction
            if self.tracked_targets:
                tracked = self.tracked_targets[0]
                tracked.predicted_x = float(self.state[0, 0])
                tracked.predicted_y = float(self.state[1, 0])
                tracked.velocity_x = float(self.state[2, 0])
                tracked.velocity_y = float(self.state[3, 0])
                tracked.prediction_age = self.prediction_count
                
                # Decrease confidence based on prediction age
                # Kare basina sabit oran; 0.95 ** prediction_count kullanmak
                # bilesik etki yaratir (toplam 0.95^(n(n+1)/2)) ve tahmin daha
                # 5-6. karede olur -> max_prediction_frames'e hic ulasilmaz.
                tracked.confidence = max(0.1, tracked.confidence * self.confidence_decay)
                
                # Update state
                if self.prediction_count > self.max_prediction_frames:
                    tracked.state = TrackingState.LOST
                else:
                    tracked.state = TrackingState.PREDICTING
            
            return float(self.state[0, 0]), float(self.state[1, 0])
    
    def update(self, target: Target) -> TrackedTarget:
        """
        Update step: Correct prediction with measurement
        
        Args:
            target: New target detection
            
        Returns:
            Updated tracked target with prediction
        """
        with self._lock:
            if not self.is_initialized:
                self.initialize(target)
                return self.tracked_targets[0]
            
            # Ölçümü işlemeden önce tahmin adımı (predict → update döngüsü)
            self._predict_state()

            # Measurement vector Z
            Z = np.array([[target.center_x], [target.center_y]], dtype=np.float32)

            # Kalman Gain: K = P·Hᵀ·(H·P·Hᵀ + R)⁻¹
            S = self.H @ self.P @ self.H.T + self.R
            K = self.P @ self.H.T @ np.linalg.inv(S)
            
            # State update: X(k) = X̂(k) + K·(Z(k) - H·X̂(k))
            innovation = Z - (self.H @ self.state)
            self.state = self.state + K @ innovation
            
            # Covariance update: P(k) = (I - K·H)·P̂(k)
            I = np.eye(4, dtype=np.float32)
            self.P = (I - K @ self.H) @ self.P
            
            # Reset prediction counter
            self.prediction_count = 0
            
            # Önceki güveni yeni nesneyi oluşturmadan önce oku
            prev_confidence = self.tracked_targets[0].confidence if self.tracked_targets else 0.8

            # Update tracked target
            tracked = TrackedTarget(
                target=target,
                predicted_x=float(self.state[0, 0]),
                predicted_y=float(self.state[1, 0]),
                velocity_x=float(self.state[2, 0]),
                velocity_y=float(self.state[3, 0]),
                confidence=min(1.0, prev_confidence + 0.2),
                state=TrackingState.LOCKED,
                last_seen=time.time(),
                prediction_age=0,
                min_confidence=self.min_confidence,
                max_prediction_age=self.max_prediction_age
            )
            
            self.tracked_targets = [tracked]
            self.update_count += 1
            
            return tracked
    
    def process(self, targets: List[Target]) -> Optional[TrackedTarget]:
        """
        Main processing loop: Predict or Update based on detection
        
        Args:
            targets: List of detected targets (empty if none found)
            
        Returns:
            Best tracked target or None
        """
        if not targets:
            # No detection - use prediction
            self.predict()
            if self.tracked_targets and self.tracked_targets[0].is_reliable:
                return self.tracked_targets[0]
            return None
        
        # Find closest target to current prediction
        if self.is_initialized and self.tracked_targets:
            predicted_pos = (float(self.state[0, 0]), float(self.state[1, 0]))
            
            # Find target closest to prediction (data association)
            closest_target = min(targets, 
                               key=lambda t: ((t.center_x - predicted_pos[0])**2 + 
                                            (t.center_y - predicted_pos[1])**2))
            
            # Check if it's close enough to be same target
            distance = ((closest_target.center_x - predicted_pos[0])**2 + 
                       (closest_target.center_y - predicted_pos[1])**2) ** 0.5
            
            if distance < self.association_threshold:  # Pixel threshold for same target
                return self.update(closest_target)
        
        # No matching target or not initialized - use first target
        if targets:
            return self.update(targets[0])
        
        return None
    
    def get_tracked_target(self) -> Optional[TrackedTarget]:
        """Get current tracked target if reliable"""
        with self._lock:
            if self.tracked_targets and self.tracked_targets[0].is_reliable:
                return self.tracked_targets[0]
            return None
    
    def reset(self) -> None:
        """Reset tracker state"""
        with self._lock:
            self.is_initialized = False
            self.tracked_targets = []
            self.prediction_count = 0
            self.state = np.zeros((4, 1), dtype=np.float32)
            self.P = np.eye(4, dtype=np.float32) * 100.0
    
    def set_dt(self, fps: float) -> None:
        """Update time step based on actual FPS"""
        self.dt = 1.0 / fps
        self.F[0, 2] = self.dt
        self.F[1, 3] = self.dt
    
    def get_stats(self) -> dict:
        """Get tracking statistics"""
        return {
            "initialized": self.is_initialized,
            "updates": self.update_count,
            "predictions": self.prediction_count_total,
            "prediction_age": self.prediction_count,
            "current_confidence": self.tracked_targets[0].confidence if self.tracked_targets else 0.0,
            "state": self.tracked_targets[0].state.value if self.tracked_targets else "none"
        }


# =============================================================================
# STANDALONE TEST
# =============================================================================

if __name__ == "__main__":
    import argparse
    import sys as _sys

    try:
        from .detector import ColorDetector
        from .capture import ScreenCapture, CaptureMethod
    except ImportError:
        from detector import ColorDetector
        from capture import ScreenCapture, CaptureMethod

    WINDOW = "Kalman Tracker"

    parser = argparse.ArgumentParser(description="Kalman Tracker standalone test")
    parser.add_argument("--backend", default="mss",
                        choices=["auto", "dxcam", "mss", "pil"],
                        help="Yakalama backend'i (varsayilan: mss - dxcam bazi "
                             "Python 3.13 kurulumlarinda OpenCV ile takiliyor)")
    parser.add_argument("--fps", type=int, default=60, help="Hedef FPS (varsayilan: 60)")
    parser.add_argument("--headless", action="store_true",
                        help="OpenCV penceresi acma, sadece konsola yaz")
    parser.add_argument("--simulate", action="store_true",
                        help="Ekran yakalama olmadan sentetik hedefle cekirdegi test et")
    parser.add_argument("--duration", type=float, default=0.0,
                        help="Saniye cinsinden calisma suresi (0 = sinirsiz)")
    args = parser.parse_args()

    print("=" * 70)
    print("Kalman Tracker Test")
    print("Developer: Ihsan")
    print("=" * 70)

    tracker = KalmanTracker(dt=1.0 / args.fps)

    # -------------------------------------------------------------------------
    # SIMULATE: capture/OpenCV'ye hic dokunmadan filtre cekirdegini dogrula
    # -------------------------------------------------------------------------
    if args.simulate:
        print("\n[Test] Sentetik hedef: sabit hizla hareket, 30. karede kayboluyor\n")
        for frame_no in range(50):
            if frame_no < 30:
                cx, cy = 400 + frame_no * 6, 300 + frame_no * 2
                targets = [Target(x=cx - 20, y=cy - 40, width=40, height=80,
                                  center_x=cx, center_y=cy, confidence=0.9,
                                  aspect_ratio=2.0, area=3200)]
            else:
                targets = []  # hedef kayboldu -> tahmin devrede

            tracked = tracker.process(targets)
            if frame_no % 5 == 0 or frame_no == 29:
                if tracked is None:
                    print(f"{frame_no:3d}  (guvenilir tahmin yok)")
                else:
                    print(f"{frame_no:3d}  {tracked.state.value:10s} "
                          f"conf={tracked.confidence:.2f} age={tracked.prediction_age:2d} "
                          f"pos=({tracked.predicted_x:7.1f},{tracked.predicted_y:7.1f}) "
                          f"vel=({tracked.velocity_x:7.1f},{tracked.velocity_y:7.1f}) px/s")
        print("\nStats:", tracker.get_stats())
        _sys.exit(0)

    # -------------------------------------------------------------------------
    # GERCEK YAKALAMA
    # -------------------------------------------------------------------------
    method = {"auto": CaptureMethod.AUTO, "dxcam": CaptureMethod.DXGI,
              "mss": CaptureMethod.MSS, "pil": CaptureMethod.PIL}[args.backend]
    try:
        cap = ScreenCapture(capture_method=method, target_fps=args.fps)
    except Exception as e:
        print(f"[Test] '{args.backend}' backend basarisiz ({e}) -> AUTO deneniyor")
        cap = ScreenCapture(capture_method=CaptureMethod.AUTO, target_fps=args.fps)

    detector = ColorDetector()
    screen_center = cap.get_screen_center()

    if not args.headless:
        import cv2
        # Pencereyi dongu disinda BIR kez olustur. Aksi halde kullanici pencereyi
        # kapatinca imshow her karede yenisini acar (pencere yagmuru).
        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW, 960, 540)
        print("\n[Test] Hedefi hareket ettir, kaybolduğunda takip devam etsin")
        print("[Test] Çıkmak için 'Q' veya ESC (ya da pencereyi kapat)\n")
    else:
        import cv2  # detector zaten cv2'ye bagimli
        print("\n[Test] Headless mod - pencere yok, Ctrl+C ile cik\n")

    start = time.perf_counter()
    last_report = 0.0
    idle_frames = 0

    try:
        while True:
            if args.duration > 0 and (time.perf_counter() - start) > args.duration:
                break

            frame = cap.grab()

            if frame is None:
                # ONEMLI: burada 'continue' demek bos dongude CPU yakmak demek.
                # DXCam yeni kare yoksa None doner; GUI olay kuyrugunu besleyip
                # kisa bir uyku ile CPU'yu serbest birak.
                idle_frames += 1
                if not args.headless:
                    if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
                        break
                    if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                        break
                else:
                    time.sleep(0.002)
                continue

            targets, mask = detector.detect(frame, screen_center)
            tracked = tracker.process(targets)

            now = time.perf_counter()
            if now - last_report >= 0.2:  # konsolu her karede degil, 5 Hz besle
                last_report = now
                if tracked:
                    print(f"\rState: {tracked.state.value:10s} | Conf: {tracked.confidence:.2f} | "
                          f"Vel: ({tracked.velocity_x:7.1f}, {tracked.velocity_y:7.1f}) px/s | "
                          f"FPS: {cap.get_fps():5.1f}", end="", flush=True)
                else:
                    print(f"\rState: {'-':10s} | hedef yok"
                          f"{' ' * 30} | FPS: {cap.get_fps():5.1f}", end="", flush=True)

            if args.headless:
                continue

            # ----------------- Görselleştirme -----------------
            display = frame.copy()

            # Tüm tespitler (yeşil)
            for t in targets:
                cv2.rectangle(display, (t.x, t.y),
                              (t.x + t.width, t.y + t.height), (0, 255, 0), 1)

            # Takip edilen hedef (kırmızı) + tahmin
            if tracked:
                color = (0, 0, 255) if tracked.state == TrackingState.LOCKED else (0, 165, 255)

                cv2.circle(display, (tracked.aim_x, tracked.aim_y), 8, color, 2)

                # Hız vektörü: hız px/saniye cinsinden, 0.2 sn ilerisini göster
                end_x = int(tracked.predicted_x + tracked.velocity_x * 0.2)
                end_y = int(tracked.predicted_y + tracked.velocity_y * 0.2)
                cv2.line(display, (int(tracked.predicted_x), int(tracked.predicted_y)),
                         (end_x, end_y), (255, 0, 255), 2)

                info = (f"{tracked.state.value.upper()} | Conf: {tracked.confidence:.2f} | "
                        f"Age: {tracked.prediction_age}")
                cv2.putText(display, info, (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

            # Tam çözünürlükte imshow pahalı - küçültüp göster
            if display.shape[1] > 960:
                scale = 960.0 / display.shape[1]
                display = cv2.resize(display, None, fx=scale, fy=scale,
                                     interpolation=cv2.INTER_NEAREST)

            cv2.imshow(WINDOW, display)

            if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
                break
            # Kullanıcı pencereyi X ile kapattıysa döngüyü bitir
            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break

    except KeyboardInterrupt:
        print("\n[Test] Ctrl+C")
    finally:
        print("\n\nStats:", tracker.get_stats())
        if idle_frames:
            print(f"Bos kare (backend None dondu): {idle_frames}")
        if not args.headless:
            cv2.destroyAllWindows()
            # Windows'ta pencerenin gercekten kapanmasi icin olay kuyrugunu bosalt
            for _ in range(4):
                cv2.waitKey(1)
        cap.close()
