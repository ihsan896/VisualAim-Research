"""
VisualAim-Research: UI Package
==============================
Yapılandırma, loglama ve terminal arayüzü modülleri.

Modüller:
- logger: Renkli terminal logu, JSON metrik toplama (ResearchLogger)
- config_manager: INI yapılandırma + JSON profil yönetimi (ConfigManager)
- menu: Terminal tabanlı etkileşimli menü (TerminalMenu)

Kullanım:
    from ui import get_logger, Colors, PerformanceMetrics
    from ui import ConfigManager
    from ui import TerminalMenu

Yazar: İhsan
Versiyon: 1.0.0
"""

# Loglama
from .logger import (
    get_logger,
    log_function,
    ResearchLogger,
    MetricsCollector,
    PerformanceMetrics,
    LogLevel,
    Colors,
)

# Yapılandırma yönetimi
from .config_manager import (
    ConfigManager,
    ConfigSchema,
    ConfigType,
    ConfigError,
    ConfigValidationError,
    ConfigFileNotFoundError,
)

# Terminal menüsü
# NOT: menu.py modül seviyesinde 'keyboard' paketini import eder. Paket kurulu
# değilse sadece TerminalMenu kullanılamaz; logger ve config_manager çalışmaya
# devam eder (modules/anti_ban.py gibi yerler ui.logger'a bağımlı).
try:
    from .menu import TerminalMenu, MenuItem, MenuState
    _MENU_AVAILABLE = True
except ImportError as _menu_err:  # pragma: no cover - opsiyonel bağımlılık
    TerminalMenu = None
    MenuItem = None
    MenuState = None
    _MENU_AVAILABLE = False
    _MENU_IMPORT_ERROR = _menu_err

# Export listesi
__all__ = [
    # Loglama
    'get_logger',
    'log_function',
    'ResearchLogger',
    'MetricsCollector',
    'PerformanceMetrics',
    'LogLevel',
    'Colors',

    # Yapılandırma
    'ConfigManager',
    'ConfigSchema',
    'ConfigType',
    'ConfigError',
    'ConfigValidationError',
    'ConfigFileNotFoundError',

    # Menü
    'TerminalMenu',
    'MenuItem',
    'MenuState',
]

# Paket metadata
__version__ = '1.0.0'
__author__ = 'İhsan'
__description__ = 'VisualAim-Research UI Package'

# Python 3.13 uyumluluk notu
import sys
if sys.version_info < (3, 10):
    raise ImportError(
        f"Python {sys.version_info.major}.{sys.version_info.minor} desteklenmiyor. "
        "Python 3.10+ gerekli."
    )
