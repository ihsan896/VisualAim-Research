"""
VisualAim-Research: Aim Controller Module
========================================
Aimbot beyni. Detector'dan hedef alıp, InputController'a hareket komutu üretir.

Özellikler:
- FOV (Field of View) sınırlama
- Smooth / Snap / Hybrid aim modları
- Recoil (geri tepme) kompanzasyonu
- İstatistik takibi

Yazar: İhsan
Versiyon: 1.0.0
"""

import math
import time
import threading
from typing import Optional, Tuple, Dict, Any, Callable
from dataclasses import dataclass
from enum import Enum

# Mevcut modüllerden import
try:
    from core.input_controller import InputController, MoveType
    from core.detector import Target
except ImportError:
    # Standalone test için relative import
    from input_controller import InputController, MoveType
    from detector import Target


class AimMode(Enum):
    """Aim modları"""
    SMOOTH = "smooth"      # Yumuşak, insan benzeri
    SNAP = "snap"          # Anlık teleport
    HYBRID = "hybrid"      # Mesafeye göre otomatik


@dataclass
class AimStats:
    """Aim istatistikleri"""
    total_targets: int = 0
    total_movements: int = 0
    total_fov_rejects: int = 0
    avg_distance: float = 0.0
    last_process_time: float = 0.0
    current_recoil_y: float = 0.0
    current_recoil_x: float = 0.0


class AimController:
    """
    AimController - Nişan alma kontrolcüsü
    
    Detector'dan gelen hedef verilerini işleyip, InputController'a
    optimize edilmiş hareket komutları gönderir.
    
    Attributes:
        config: Yapılandırma sözlüğü
        input_controller: Fare kontrolcüsü instance'ı
        mode: Mevcut aim modu (smooth/snap/hybrid)
        stats: Performans istatistikleri
        _lock: Thread güvenliği için kilit
    """
    
    def __init__(
        self,
        config: Dict[str, Any],
        input_controller: InputController
    ):
        """
        AimController başlatıcı
        
        Args:
            config: Yapılandırma sözlüğü
                - aim_speed: float (0.1-1.0) - Hareket hızı
                - smoothing: float (0.0-1.0) - Yumuşaklık faktörü
                - fov_radius: int (50-500) - FOV yarıçapı (piksel)
                - head_offset: float (0.0-1.0) - Kafa ofseti
                - recoil_y: float - Dikey recoil miktarı
                - recoil_x: float - Yatay recoil miktarı
                - recoil_recovery: float (0.0-1.0) - Recoil geri kazanım hızı
                - aim_mode: str ("smooth", "snap", "hybrid")
                - hybrid_threshold: int - Hybrid mod eşik mesafesi (piksel)
            input_controller: InputController instance'ı
        """
        self.config = config
        self.input_controller = input_controller
        
        # Varsayılan değerler
        self.aim_speed = config.get('aim_speed', 0.45)
        self.smoothing = config.get('smoothing', 0.6)
        self.fov_radius = config.get('fov_radius', 250)
        self.head_offset = config.get('head_offset', 0.43)
        self.recoil_y = config.get('recoil_y', 2.0)
        self.recoil_x = config.get('recoil_x', 0.0)
        self.recoil_recovery = config.get('recoil_recovery', 0.1)
        self.hybrid_threshold = config.get('hybrid_threshold', 150)
        
        # Mod ayarı
        mode_str = config.get('aim_mode', 'smooth').lower()
        self.mode = AimMode(mode_str) if mode_str in ['smooth', 'snap', 'hybrid'] else AimMode.SMOOTH
        
        # İstatistikler
        self.stats = AimStats()
        self._distance_history = []  # Ortalama mesafe hesabı için
        self._max_history = 10
        
        # Thread güvenliği
        self._lock = threading.RLock()
        
        # Son hareket zamanı (smooth için)
        self._last_move_time = 0.0
        
        print(f"[AimController] Initialized | Mode: {self.mode.value} | FOV: {self.fov_radius}px")
    
    def calculate_movement(
        self,
        target: Optional[Target],
        screen_center: Tuple[int, int],
        capture_offset: Tuple[int, int] = (0, 0),
        movement_filter: Optional[Callable[[float, float], Tuple[float, float]]] = None,
        execute: bool = True
    ) -> Optional[Tuple[int, int]]:
        """
        Hedef için hareket komutu hesapla ve uygula

        Args:
            target: Detector'dan gelen hedef (None ise recoil recovery)
            screen_center: Ekran merkezi koordinatları (x, y)
            capture_offset: Capture bölgesinin ekran offset'i (x, y)
            movement_filter: (dx, dy) -> (dx, dy) dönüşümü; hareket
                uygulanmadan hemen önce çağrılır (ör. Humanizer jitter'ı).
                Böylece çağıranın ikinci kez fare hareketi uygulaması
                gerekmez - aynı hareket iki kez uygulanıyordu.
            execute: False ise fare hareketi uygulanmaz, yalnızca delta döner

        Returns:
            Tuple[int, int]: Uygulanan delta (dx, dy) veya None
        """
        with self._lock:
            start_time = time.perf_counter()
            
            # Recoil recovery (her frame'de azalt)
            self._apply_recoil_recovery()
            
            if target is None:
                self.stats.last_process_time = time.perf_counter() - start_time
                return None
            
            self.stats.total_targets += 1
            
            # 1. Hedefin gerçek ekran koordinatlarını hesapla
            # Detector'dan gelen koordinatlar FOV içinde relative
            # capture_offset ekleyerek global ekran koordinatlarına çevir
            # Not: Yatayda hedefin merkezi (head_x), dikeyde ise config'te
            # ayarlanabilen head_offset oranı kullanılır (detector.py'nin
            # sabit 0.28 oranını AimController seviyesinde ezerek
            # kullanıcıya kafa noktasını profil bazında ayarlama imkanı verir).
            target_screen_x = capture_offset[0] + target.head_x
            target_screen_y = capture_offset[1] + int(target.y + target.height * self.head_offset)

            
            # 2. Delta hesapla (ekran merkezinden hedefe olan mesafe)
            dx = target_screen_x - screen_center[0]
            dy = target_screen_y - screen_center[1]
            
            # 3. FOV (Field of View) kontrolü
            distance = math.sqrt(dx * dx + dy * dy)
            
            if distance > self.fov_radius:
                self.stats.total_fov_rejects += 1
                self.stats.last_process_time = time.perf_counter() - start_time
                return None
            
            # Mesafe geçmişini güncelle (istatistik için)
            self._update_distance_history(distance)
            
            # 4. Recoil kompanzasyonu ekle
            # Recoil aşağıya doğru olduğu için yukarı çekmek gerek (negatif)
            dy -= self.stats.current_recoil_y
            dx -= self.stats.current_recoil_x
            
            # 5. Mod seçimi ve hareket uygulama
            move_type = self._select_move_type(distance)
            
            # 6. Hız faktörü uygula
            if move_type == MoveType.SMOOTH:
                # Smooth mod: hız ve smoothing faktörü ile çarp
                dx *= self.aim_speed * (1.0 - self.smoothing * 0.5)
                dy *= self.aim_speed * (1.0 - self.smoothing * 0.5)
            elif move_type == MoveType.INSTANT:
                # Snap mod: direkt hız faktörü
                dx *= self.aim_speed
                dy *= self.aim_speed
            
            # 7. İnsansı sapma (varsa) ve fare hareketini uygula
            if movement_filter is not None:
                try:
                    dx, dy = movement_filter(dx, dy)
                except Exception as e:
                    print(f"[AimController] movement_filter error: {e}")

            if execute:
                self._execute_movement(dx, dy, move_type)

            # İstatistikleri güncelle
            self.stats.total_movements += 1
            self.stats.last_process_time = time.perf_counter() - start_time
            
            return (int(dx), int(dy))
    
    def _select_move_type(self, distance: float) -> MoveType:
        """
        Mesafeye göre hareket tipi seç (özellikle Hybrid mod için)
        
        Args:
            distance: Hedefe olan piksel mesafesi
        
        Returns:
            MoveType: Seçilen hareket tipi
        """
        if self.mode == AimMode.SMOOTH:
            return MoveType.SMOOTH
        elif self.mode == AimMode.SNAP:
            return MoveType.INSTANT
        elif self.mode == AimMode.HYBRID:
            # Hybrid: Yakınsa smooth, uzaktan snap
            if distance < self.hybrid_threshold:
                return MoveType.SMOOTH
            else:
                return MoveType.INSTANT
        
        return MoveType.SMOOTH
    
    def _execute_movement(self, dx: float, dy: float, move_type: MoveType) -> None:
        """
        InputController üzerinden hareketi uygula
        
        Args:
            dx: X ekseni delta
            dy: Y ekseni delta
            move_type: Hareket tipi
        """
        # Her mod için tek, BLOKLAMAYAN hareket.
        #
        # Eskiden smooth mod move_smooth(duration_ms=50) çağırıyordu; bu
        # fonksiyon içeride time.sleep ile ~54 ms bekliyor ve ana döngüyü
        # kilitliyordu (hedef görünürken tavan ~18 FPS). Yumuşatma zaten
        # calculate_movement içinde yapılıyor: her karede hedefe olan
        # mesafenin aim_speed × (1 - smoothing/2) oranı kadar ilerleniyor.
        # Yani kapalı döngü kendisi yumuşatıyor; ikinci bir easing katmanı
        # hem gereksiz hem de gecikme kaynağıydı.
        #
        # float geçiliyor: InputController alt-piksel artıklarını biriktirir,
        # int() ile kırpınca 1 pikselin altındaki düzeltmeler kayboluyordu.
        self.input_controller.move_relative(dx, dy)
    
    def _apply_recoil_recovery(self) -> None:
        """
        Recoil'i zamanla azalt (geri kazanım)
        """
        # abs() ile karsilastirilmali: recoil_x negatif ayarlandiginda
        # (sola kacan silah) '> 0' kosulu hic saglanmiyor, geri kazanim
        # calismiyor ve deger sinirsiz birikiyordu.
        if abs(self.stats.current_recoil_y) > 0:
            self.stats.current_recoil_y *= (1.0 - self.recoil_recovery)
            if abs(self.stats.current_recoil_y) < 0.1:
                self.stats.current_recoil_y = 0.0

        if abs(self.stats.current_recoil_x) > 0:
            self.stats.current_recoil_x *= (1.0 - self.recoil_recovery)
            if abs(self.stats.current_recoil_x) < 0.1:
                self.stats.current_recoil_x = 0.0
    
    def _update_distance_history(self, distance: float) -> None:
        """
        Mesafe geçmişini güncelle ve ortalama hesapla
        
        Args:
            distance: Yeni mesafe değeri
        """
        self._distance_history.append(distance)
        if len(self._distance_history) > self._max_history:
            self._distance_history.pop(0)
        
        self.stats.avg_distance = sum(self._distance_history) / len(self._distance_history)
    
    def update_recoil(self, fired: bool) -> None:
        """
        Ateş edildiğinde recoil değerlerini güncelle
        
        Args:
            fired: Ateş edildi mi?
        """
        with self._lock:
            if fired:
                self.stats.current_recoil_y += self.recoil_y
                self.stats.current_recoil_x += self.recoil_x
                
                # Maksimum recoil sınırı (negatif yön de sınırlanmalı; tek
                # yonlu min() negatif recoil'in sinirsiz buyumesine izin verir)
                max_recoil = 50.0  # piksel
                self.stats.current_recoil_y = max(-max_recoil,
                                                  min(self.stats.current_recoil_y, max_recoil))
                self.stats.current_recoil_x = max(-max_recoil,
                                                  min(self.stats.current_recoil_x, max_recoil))
    
    def set_mode(self, mode: str) -> None:
        """
        Aim modunu değiştir
        
        Args:
            mode: "smooth", "snap", veya "hybrid"
        """
        try:
            self.mode = AimMode(mode.lower())
            print(f"[AimController] Mode changed to: {self.mode.value}")
        except ValueError:
            print(f"[AimController] Invalid mode: {mode}. Keeping {self.mode.value}")
    
    def set_config(self, key: str, value: Any) -> None:
        """
        Runtime yapılandırma güncelleme
        
        Args:
            key: Config anahtarı
            value: Yeni değer
        """
        with self._lock:
            if hasattr(self, key):
                setattr(self, key, value)
                print(f"[AimController] Config updated: {key} = {value}")
    
    def get_stats(self) -> Dict[str, Any]:
        """
        İstatistikleri sözlük olarak döndür
        
        Returns:
            Dict: İstatistikler
        """
        with self._lock:
            return {
                'total_targets': self.stats.total_targets,
                'total_movements': self.stats.total_movements,
                'total_fov_rejects': self.stats.total_fov_rejects,
                'avg_distance': round(self.stats.avg_distance, 2),
                'current_recoil_y': round(self.stats.current_recoil_y, 2),
                'current_recoil_x': round(self.stats.current_recoil_x, 2),
                'last_process_time_ms': round(self.stats.last_process_time * 1000, 2),
                'current_mode': self.mode.value
            }
    
    def reset_stats(self) -> None:
        """İstatistikleri sıfırla"""
        with self._lock:
            self.stats = AimStats()
            self._distance_history.clear()
            print("[AimController] Stats reset")


# =============================================================================
# TEST BLOĞU
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("AimController Test")
    print("=" * 60)
    
    # Mock InputController (gerçek fare hareketi yapmadan test)
    class MockInputController:
        def __init__(self):
            self.last_dx = 0
            self.last_dy = 0
            self.last_type = None
        
        def move_relative(self, dx: int, dy: int) -> None:
            self.last_dx = dx
            self.last_dy = dy
            self.last_type = MoveType.INSTANT
            print(f"  [Mock] SNAP move: dx={dx:+4d}, dy={dy:+4d}")
        
        def move_smooth(self, dx: int, dy: int, duration_ms: int = 50) -> None:
            self.last_dx = dx
            self.last_dy = dy
            self.last_type = MoveType.SMOOTH
            print(f"  [Mock] SMOOTH move: dx={dx:+4d}, dy={dy:+4d} ({duration_ms}ms)")
        
        def move_to_target(self, dx: int, dy: int, move_type: MoveType = MoveType.BEZIER) -> None:
            self.last_dx = dx
            self.last_dy = dy
            self.last_type = move_type
            print(f"  [Mock] BEZIER move: dx={dx:+4d}, dy={dy:+4d}")
    
    # Test config
    test_config = {
        'aim_speed': 0.5,
        'smoothing': 0.6,
        'fov_radius': 250,
        'head_offset': 0.43,
        'recoil_y': 2.0,
        'recoil_x': 0.5,
        'recoil_recovery': 0.1,
        'aim_mode': 'hybrid',
        'hybrid_threshold': 150
    }
    
    # Mock Target oluştur
    # NOT: Target (core/detector.py) dataclass'ı center_x, center_y ve
    # aspect_ratio alanlarını da ZORUNLU ister; head_x/head_y ise salt-okunur
    # @property'dir (center_x/y ve y+height'dan hesaplanır), bu yüzden
    # doğrudan atama (mock_target.head_x = ...) AttributeError fırlatır.
    mock_target = Target(
        x=100, y=100, width=50, height=100,
        center_x=125, center_y=150,
        confidence=0.85, aspect_ratio=2.0, area=5000
    )

    
    # Test başlat
    mock_input = MockInputController()
    aim = AimController(test_config, mock_input)
    
    screen_center = (640, 360)  # 1280x720 ekran merkezi
    capture_offset = (300, 200)  # FOV offset
    
    print(f"\n[Test] Target: ({mock_target.head_x}, {mock_target.head_y})")
    print(f"[Test] Screen Center: {screen_center}")
    print(f"[Test] Capture Offset: {capture_offset}")
    print(f"[Test] Mode: {aim.mode.value}")
    print("-" * 40)
    
    # Test 1: Normal aim
    print("\n[Test 1] Normal aim (HYBRID mode):")
    result = aim.calculate_movement(mock_target, screen_center, capture_offset)
    print(f"  Result: {result}")
    
    # Test 2: Recoil ekle ve tekrar aim
    print("\n[Test 2] After recoil (simulated 3 shots):")
    for i in range(3):
        aim.update_recoil(fired=True)
        print(f"  Recoil Y: {aim.stats.current_recoil_y:.2f}")
    
    result = aim.calculate_movement(mock_target, screen_center, capture_offset)
    print(f"  Result with recoil: {result}")
    
    # Test 3: FOV dışı hedef
    print("\n[Test 3] Target outside FOV:")
    far_target = Target(
        x=1000, y=1000, width=50, height=100,
        center_x=1025, center_y=1050,
        confidence=0.9, aspect_ratio=2.0, area=5000
    )
    result = aim.calculate_movement(far_target, screen_center, capture_offset)

    print(f"  Result (should be None): {result}")
    print(f"  FOV Rejects: {aim.stats.total_fov_rejects}")
    
    # Test 4: Mod değiştir
    print("\n[Test 4] Mode switching:")
    aim.set_mode("snap")
    result = aim.calculate_movement(mock_target, screen_center, capture_offset)
    
    aim.set_mode("smooth")
    result = aim.calculate_movement(mock_target, screen_center, capture_offset)
    
    # Test 5: İstatistikler
    print("\n[Test 5] Statistics:")
    stats = aim.get_stats()
    for key, value in stats.items():
        print(f"  {key}: {value}")
    
    print("\n" + "=" * 60)
    print("Test completed successfully!")
    print("=" * 60) 
