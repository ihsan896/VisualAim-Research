"""
VisualAim-Research: Core Package
================================
Aimbot sisteminin çekirdek modülleri.

Modüller:
- capture: Ekran yakalama (DXCam/MSS)
- detector: Renk tabanlı hedef tespiti (HSV)
- input_controller: Donanım seviyesi fare kontrolü (WinAPI)
- aim_controller: Nişan alma algoritmaları (FOV, Recoil, Smooth/Snap/Hybrid)
- trigger: Triggerbot (otomatik ateş)
- kalman_tracker: Hedef takip (Kalman filtresi)

Kullanım:
    from core import ScreenCapture, ColorDetector, InputController
    from core import AimController, TriggerBot, KalmanTracker
    from core import Target, MoveType, AimMode

Yazar: İhsan
Versiyon: 1.0.0
"""

# Ekran yakalama
from .capture import ScreenCapture

# Hedef tespiti
from .detector import ColorDetector, Target

# Fare kontrolü
from .input_controller import InputController, MoveType

# Nişan alma kontrolcüsü
from .aim_controller import AimController, AimMode, AimStats

# Triggerbot
from .trigger import TriggerBot, TriggerConfig, TriggerMode, TriggerWeapon

# Kalman takip
from .kalman_tracker import KalmanTracker, TrackedTarget, TrackingState

# Export listesi
__all__ = [
    # Sınıflar
    'ScreenCapture',
    'ColorDetector',
    'InputController',
    'AimController',
    'TriggerBot',
    'KalmanTracker',

    # Dataclass'lar
    'Target',
    'AimStats',
    'TrackedTarget',
    'TriggerConfig',

    # Enum'lar
    'MoveType',
    'AimMode',
    'TrackingState',
    'TriggerMode',
    'TriggerWeapon',
]

# Paket metadata
__version__ = '1.0.0'
__author__ = 'İhsan'
__description__ = 'VisualAim-Research Core Package'

# Python 3.13 uyumluluk notu
import sys
if sys.version_info < (3, 10):
    raise ImportError(
        f"Python {sys.version_info.major}.{sys.version_info.minor} desteklenmiyor. "
        "Python 3.10+ gerekli."
    )

print(f"[Core Package] VisualAim-Research v{__version__} loaded")
print(f"[Core Package] Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
print(f"[Core Package] Available modules: {len(__all__)} exports")