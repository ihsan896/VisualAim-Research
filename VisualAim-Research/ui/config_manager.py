#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VisualAim-Research Configuration Manager
========================================
Merkezi yapılandırma yönetim sistemi. INI dosya formatı, tip güvenliği,
otomatik validasyon ve değişiklik izleme özellikleri.

Özellikler:
- INI formatı (research_config.ini)
- JSON profil desteği (profiles/*.json)
- Tip dönüşümü (int, float, bool, list, tuple, Path)
- Varsayılan değerler ve validasyon
- File watcher (değişiklik algılama)
- Thread-safe erişim
- Section bazlı organizasyon

Author: İhsan
Version: 2.0.0
"""

import json
import configparser
import threading
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union, Optional, Callable
from dataclasses import dataclass
from enum import Enum, auto
from functools import wraps
import time


# Logger yapılandırması
logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Yapılandırma hatası temel sınıfı"""
    pass


class ConfigValidationError(ConfigError):
    """Validasyon hatası"""
    pass


class ConfigFileNotFoundError(ConfigError):
    """Dosya bulunamadı hatası"""
    pass


class ConfigType(Enum):
    """Desteklenen yapılandırma tipleri"""
    STRING = auto()
    INTEGER = auto()
    FLOAT = auto()
    BOOLEAN = auto()
    LIST = auto()
    TUPLE = auto()
    PATH = auto()
    COLOR_RANGE = auto()  # Özel: HSV renk aralığı


@dataclass
class ConfigSchema:
    """Yapılandırma şeması tanımı"""
    section: str
    key: str
    config_type: ConfigType
    default: Any
    min_value: Optional[Union[int, float]] = None
    max_value: Optional[Union[int, float]] = None
    choices: Optional[List[Any]] = None
    description: str = ""


def synchronized(lock_attr: str = "_lock"):
    """Thread-safe decorator"""
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            lock = getattr(self, lock_attr)
            with lock:
                return func(self, *args, **kwargs)
        return wrapper
    return decorator


# ============================================================
# JSON profil -> INI şema eşlemesi
# ============================================================
# profiles/*.json dosyaları iç içe (nested) yapıdadır, INI şeması ise düz
# "section.key" biçimindedir. İkisi doğrudan uyuşmadığı için load_profile()
# bu tabloyu kullanarak çeviri yapar.
#
# Sol taraf: JSON içindeki nokta ile ayrılmış yol (liste öğesi için sayı).
# Sağ taraf: DEFAULT_SCHEMA içindeki hedef anahtar.
PROFILE_KEY_MAP: List[Tuple[str, str]] = [
    # Renk tespiti - birincil HSV aralığı
    ("color_detection.ranges.0.lower.0", "color.lower_h"),
    ("color_detection.ranges.0.lower.1", "color.lower_s"),
    ("color_detection.ranges.0.lower.2", "color.lower_v"),
    ("color_detection.ranges.0.upper.0", "color.upper_h"),
    ("color_detection.ranges.0.upper.1", "color.upper_s"),
    ("color_detection.ranges.0.upper.2", "color.upper_v"),

    # Renk tespiti - ikincil HSV aralığı (kırmızı gibi sarmalayan tonlar için)
    ("color_detection.ranges.1.lower.0", "color.lower_h2"),
    ("color_detection.ranges.1.lower.1", "color.lower_s2"),
    ("color_detection.ranges.1.lower.2", "color.lower_v2"),
    ("color_detection.ranges.1.upper.0", "color.upper_h2"),
    ("color_detection.ranges.1.upper.1", "color.upper_s2"),
    ("color_detection.ranges.1.upper.2", "color.upper_v2"),

    # Blob filtreleri
    ("color_detection.filters.min_area", "color.min_blob_size"),
    ("color_detection.filters.max_area", "color.max_blob_size"),

    # Nişan alma
    ("aim_settings.enabled", "aim.enabled"),
    ("aim_settings.mode", "aim.mode"),
    ("aim_settings.speed", "aim.speed"),
    ("aim_settings.smoothing", "aim.smoothing"),
    ("aim_settings.fov.radius_x", "aim.fov_radius"),
    ("aim_settings.head_offset.value", "aim.head_offset"),
    ("kalman_settings.enabled", "aim.use_kalman"),

    # Triggerbot
    ("trigger_settings.enabled", "trigger.enabled"),
    ("trigger_settings.mode", "trigger.mode"),
    ("trigger_settings.threshold", "trigger.threshold"),
    ("trigger_settings.delay.min_ms", "trigger.delay_min"),
    ("trigger_settings.delay.max_ms", "trigger.delay_max"),
    ("trigger_settings.first_shot_delay_ms", "trigger.first_shot_delay"),

    # Geri tepme telafisi
    # NOT: recoil.weapon bilerek eşlenmedi - şema seçenekleri Valorant silahlarıyla
    # sınırlı (vandal/phantom/spectre/bulldog/ares/odin), CS2 silah adları geçersiz
    # olurdu. Silah paternleri zaten JSON'dan core/recoil.py tarafından okunuyor.
    ("recoil_settings.enabled", "recoil.enabled"),
    ("recoil_settings.compensation.strength", "recoil.compensation_x"),
    ("recoil_settings.compensation.strength", "recoil.compensation_y"),
    ("recoil_settings.compensation.recover", "recoil.recovery_rate"),

    # Fare girişi
    ("aim_settings.y_speed_multiplier", "input.y_multiplier"),
    ("anti_detection.humanization.enabled", "input.micro_jitter"),
    ("anti_detection.humanization.aim_jitter", "input.jitter_amount"),

    # Kalman takibi
    ("kalman_settings.process_noise", "kalman.process_noise"),
    ("kalman_settings.measurement_noise", "kalman.measurement_noise"),
    ("kalman_settings.max_predictions", "kalman.max_prediction_frames"),

    # Yakalama
    # NOT: screen.capture_fov_* bilerek eşlenmedi - profillerde tam ekran (1920x1080)
    # olarak duruyor, capture.fov_* ise merkez etrafındaki küçük yakalama bölgesi.
    # Eşlenirse tüm ekran 60 FPS yakalanmaya çalışılır ve performans çöker.
    ("performance.capture_fps", "capture.target_fps"),

    # Oyun penceresi (profildeki oyun adı pencere başlığı olarak kullanılır)
    ("game.name", "game.window_titles"),

    # Hata ayıklama
    ("debug.enabled", "debug.enabled"),
    ("debug.show_target", "debug.show_overlay"),
    ("debug.save_screenshots", "debug.save_screenshots"),
]

# Profil dosyalarındaki değerlerin şema seçeneklerine çevrimi.
# Örn. JSON'da "hold" yazar, şema "hold_key" bekler.
PROFILE_VALUE_MAP: Dict[str, Dict[Any, Any]] = {
    "trigger.mode": {
        "hold": "hold_key",
        "always": "always_on",
        "aim": "aim_key",
    },
}

# save_profile() için ters çevrim (şema değeri -> profil dosyası değeri)
PROFILE_VALUE_MAP_REVERSE: Dict[str, Dict[Any, Any]] = {
    config_key: {schema_value: json_value for json_value, schema_value in mapping.items()}
    for config_key, mapping in PROFILE_VALUE_MAP.items()
}


def _resolve_json_path(data: Any, path: str) -> Tuple[bool, Any]:
    """
    Nokta ile ayrılmış yolu iç içe dict/list yapısında çözer.

    Args:
        data: JSON'dan yüklenmiş yapı
        path: "aim_settings.fov.radius_x" veya "ranges.0.lower.1" biçiminde yol

    Returns:
        (bulundu, değer) - yol yoksa (False, None)
    """
    current = data
    for part in path.split('.'):
        if isinstance(current, dict):
            if part not in current:
                return False, None
            current = current[part]
        elif isinstance(current, list):
            if not part.isdigit() or int(part) >= len(current):
                return False, None
            current = current[int(part)]
        else:
            return False, None
    return True, current


def _set_json_path(root: Dict[str, Any], path: str, value: Any) -> None:
    """
    Nokta ile ayrılmış yola değer yazar, ara kapsayıcıları oluşturur.

    _resolve_json_path() işleminin tersidir; save_profile() bunu kullanarak
    düz "section.key" değerlerinden iç içe profil dosyası üretir.
    Sayısal bölümler liste indeksi olarak yorumlanır:
        "color_detection.ranges.0.lower.0" -> {"color_detection": {"ranges": [{"lower": [v]}]}}
    """
    parts = path.split('.')
    current: Any = root

    for index, part in enumerate(parts[:-1]):
        # Bir sonraki bölüm sayı ise burada liste, değilse sözlük gerekiyor
        container: Any = [] if parts[index + 1].isdigit() else {}

        if isinstance(current, list):
            position = int(part)
            while len(current) <= position:
                current.append(None)
            if not isinstance(current[position], (dict, list)):
                current[position] = container
            current = current[position]
        else:
            if not isinstance(current.get(part), (dict, list)):
                current[part] = container
            current = current[part]

    last = parts[-1]
    if isinstance(current, list):
        position = int(last)
        while len(current) <= position:
            current.append(None)
        current[position] = value
    else:
        current[last] = value


class ConfigManager:
    """
    Merkezi Yapılandırma Yöneticisi
    
    Thread-safe, validasyonlu, INI/JSON destekli yapılandırma yönetimi.
    Singleton pattern uygulanabilir (opsiyonel).
    """
    
    # Varsayılan yapılandırma şeması
    DEFAULT_SCHEMA: Dict[str, ConfigSchema] = {
        # Ekran Yakalama Ayarları
        "capture.backend": ConfigSchema(
            section="capture", key="backend",
            config_type=ConfigType.STRING,
            default="dxcam",
            # "auto" da geçerli: core/capture.py CaptureMethod.AUTO ile aynı değer
            choices=["auto", "dxcam", "mss", "pil"],
            description="Ekran yakalama backend'i"
        ),
        "capture.detection_scale": ConfigSchema(
            section="capture", key="detection_scale",
            config_type=ConfigType.FLOAT,
            default=1.0, min_value=0.1, max_value=1.0,
            description="Tespit öncesi küçültme oranı (tam ekranda 0.25-0.5 önerilir; "
                        "koordinatlar tam çözünürlüğe geri ölçeklenir)"
        ),
        "capture.full_screen": ConfigSchema(
            section="capture", key="full_screen",
            config_type=ConfigType.BOOLEAN,
            default=False,
            description="Tüm ekranı yakala (kapalıysa fov_x/fov_y bölgesi kullanılır)"
        ),
        "capture.fov_x": ConfigSchema(
            section="capture", key="fov_x",
            config_type=ConfigType.INTEGER,
            # Üst sınır 1000'di; 2560px genişlikte tam ekran bölge tanımlanamıyordu
            default=400, min_value=50, max_value=4000,
            description="Yatay yakalama bölgesi genişliği (piksel)"
        ),
        "capture.fov_y": ConfigSchema(
            section="capture", key="fov_y",
            config_type=ConfigType.INTEGER,
            default=400, min_value=50, max_value=4000,
            description="Dikey yakalama bölgesi yüksekliği (piksel)"
        ),
        "capture.offset_x": ConfigSchema(
            section="capture", key="offset_x",
            config_type=ConfigType.INTEGER,
            default=0, min_value=-500, max_value=500,
            description="X ekseni ofseti"
        ),
        "capture.offset_y": ConfigSchema(
            section="capture", key="offset_y",
            config_type=ConfigType.INTEGER,
            default=0, min_value=-500, max_value=500,
            description="Y ekseni ofseti"
        ),
        "capture.target_fps": ConfigSchema(
            section="capture", key="target_fps",
            config_type=ConfigType.INTEGER,
            default=144, min_value=30, max_value=240,
            description="Hedef FPS"
        ),
        "capture.show_fps": ConfigSchema(
            section="capture", key="show_fps",
            config_type=ConfigType.BOOLEAN,
            default=True,
            description="FPS gösterimi"
        ),
        
        # Renk Tespiti Ayarları
        "color.lower_h": ConfigSchema(
            section="color", key="lower_h",
            config_type=ConfigType.INTEGER,
            default=0, min_value=0, max_value=179,
            description="HSV Alt H değeri"
        ),
        "color.lower_s": ConfigSchema(
            section="color", key="lower_s",
            config_type=ConfigType.INTEGER,
            default=150, min_value=0, max_value=255,
            description="HSV Alt S değeri"
        ),
        "color.lower_v": ConfigSchema(
            section="color", key="lower_v",
            config_type=ConfigType.INTEGER,
            default=150, min_value=0, max_value=255,
            description="HSV Alt V değeri"
        ),
        "color.upper_h": ConfigSchema(
            section="color", key="upper_h",
            config_type=ConfigType.INTEGER,
            default=10, min_value=0, max_value=179,
            description="HSV Üst H değeri"
        ),
        "color.upper_s": ConfigSchema(
            section="color", key="upper_s",
            config_type=ConfigType.INTEGER,
            default=255, min_value=0, max_value=255,
            description="HSV Üst S değeri"
        ),
        "color.upper_v": ConfigSchema(
            section="color", key="upper_v",
            config_type=ConfigType.INTEGER,
            default=255, min_value=0, max_value=255,
            description="HSV Üst V değeri"
        ),
        "color.lower_h2": ConfigSchema(
            section="color", key="lower_h2",
            config_type=ConfigType.INTEGER,
            default=170, min_value=0, max_value=179,
            description="HSV Alt H2 değeri (ikinci aralık)"
        ),
        "color.lower_s2": ConfigSchema(
            section="color", key="lower_s2",
            config_type=ConfigType.INTEGER,
            default=150, min_value=0, max_value=255,
            description="HSV Alt S2 değeri"
        ),
        "color.lower_v2": ConfigSchema(
            section="color", key="lower_v2",
            config_type=ConfigType.INTEGER,
            default=150, min_value=0, max_value=255,
            description="HSV Alt V2 değeri"
        ),
        "color.upper_h2": ConfigSchema(
            section="color", key="upper_h2",
            config_type=ConfigType.INTEGER,
            default=179, min_value=0, max_value=179,
            description="HSV Üst H2 değeri"
        ),
        "color.upper_s2": ConfigSchema(
            section="color", key="upper_s2",
            config_type=ConfigType.INTEGER,
            default=255, min_value=0, max_value=255,
            description="HSV Üst S2 değeri"
        ),
        "color.upper_v2": ConfigSchema(
            section="color", key="upper_v2",
            config_type=ConfigType.INTEGER,
            default=255, min_value=0, max_value=255,
            description="HSV Üst V2 değeri"
        ),
        "color.min_blob_size": ConfigSchema(
            section="color", key="min_blob_size",
            config_type=ConfigType.INTEGER,
            default=30, min_value=10, max_value=500,
            description="Minimum blob boyutu"
        ),
        "color.max_blob_size": ConfigSchema(
            section="color", key="max_blob_size",
            config_type=ConfigType.INTEGER,
            default=5000, min_value=100, max_value=20000,
            description="Maksimum blob boyutu"
        ),
        
        # Aimbot Ayarları
        "aim.enabled": ConfigSchema(
            section="aim", key="enabled",
            config_type=ConfigType.BOOLEAN,
            default=True,
            description="Aimbot aktif mi"
        ),
        "aim.mode": ConfigSchema(
            section="aim", key="mode",
            config_type=ConfigType.STRING,
            default="smooth",
            choices=["smooth", "snap", "hybrid"],
            description="Aim modu"
        ),
        "aim.speed": ConfigSchema(
            section="aim", key="speed",
            config_type=ConfigType.FLOAT,
            default=0.35, min_value=0.01, max_value=1.0,
            description="Aim hızı (0-1)"
        ),
        "aim.smoothing": ConfigSchema(
            section="aim", key="smoothing",
            config_type=ConfigType.FLOAT,
            default=0.15, min_value=0.0, max_value=1.0,
            description="Yumuşatma faktörü"
        ),
        "aim.fov_radius": ConfigSchema(
            section="aim", key="fov_radius",
            config_type=ConfigType.INTEGER,
            # Üst sınır 500'dü; 2560x1440 ekranda tam ekran nişan için
            # yarı köşegen ~1470px gerekiyor, ayar sessizce kırpılıyordu
            default=250, min_value=50, max_value=2000,
            description="Aim FOV yarıçapı (tam ekran için ekran yarı köşegeni kadar olmalı)"
        ),
        "aim.head_offset": ConfigSchema(
            section="aim", key="head_offset",
            config_type=ConfigType.FLOAT,
            default=0.43, min_value=0.2, max_value=0.8,
            description="Kafa ofset çarpanı"
        ),
        "aim.hybrid_threshold": ConfigSchema(
            section="aim", key="hybrid_threshold",
            config_type=ConfigType.INTEGER,
            default=150, min_value=50, max_value=400,
            description="Hybrid mod eşik değeri"
        ),
        "aim.use_kalman": ConfigSchema(
            section="aim", key="use_kalman",
            config_type=ConfigType.BOOLEAN,
            default=True,
            description="Kalman filtresi kullan"
        ),
        
        # Triggerbot Ayarları
        "trigger.enabled": ConfigSchema(
            section="trigger", key="enabled",
            config_type=ConfigType.BOOLEAN,
            default=True,
            description="Triggerbot aktif mi"
        ),
        "trigger.mode": ConfigSchema(
            section="trigger", key="mode",
            config_type=ConfigType.STRING,
            default="hold_key",
            choices=["always_on", "hold_key", "toggle", "aim_key"],
            description="Trigger modu"
        ),
        "trigger.delay_min": ConfigSchema(
            section="trigger", key="delay_min",
            config_type=ConfigType.INTEGER,
            default=10, min_value=0, max_value=500,
            description="Minimum gecikme (ms)"
        ),
        "trigger.delay_max": ConfigSchema(
            section="trigger", key="delay_max",
            config_type=ConfigType.INTEGER,
            default=50, min_value=0, max_value=500,
            description="Maksimum gecikme (ms)"
        ),
        "trigger.threshold": ConfigSchema(
            section="trigger", key="threshold",
            config_type=ConfigType.INTEGER,
            default=8, min_value=1, max_value=50,
            description="Piksel eşik değeri"
        ),
        "trigger.first_shot_delay": ConfigSchema(
            section="trigger", key="first_shot_delay",
            config_type=ConfigType.INTEGER,
            default=0, min_value=0, max_value=200,
            description="İlk atış gecikmesi (ms)"
        ),
        
        # Recoil Ayarları
        "recoil.enabled": ConfigSchema(
            section="recoil", key="enabled",
            config_type=ConfigType.BOOLEAN,
            default=False,
            description="Recoil kontrolü aktif mi"
        ),
        "recoil.weapon": ConfigSchema(
            section="recoil", key="weapon",
            config_type=ConfigType.STRING,
            default="vandal",
            # main.py bu değerden hem TriggerWeapon hem WeaponType üretiyor, bu
            # yüzden seçenekler iki enum'un KESİŞİMİ olmalı. (bulldog/ares/odin
            # core/trigger.py TriggerWeapon içinde yok - seçilirse main.py
            # başlangıçta ValueError ile çöküyordu.)
            choices=[
                "vandal", "phantom", "spectre", "sheriff", "guardian",
                "classic", "ghost", "frenzy", "marshal", "operator",
            ],
            description="Aktif silah"
        ),
        "recoil.compensation_y": ConfigSchema(
            section="recoil", key="compensation_y",
            config_type=ConfigType.FLOAT,
            default=1.0, min_value=0.0, max_value=2.0,
            description="Dikey kompanzasyon çarpanı"
        ),
        "recoil.compensation_x": ConfigSchema(
            section="recoil", key="compensation_x",
            config_type=ConfigType.FLOAT,
            default=1.0, min_value=0.0, max_value=2.0,
            description="Yatay kompanzasyon çarpanı"
        ),
        "recoil.recovery_rate": ConfigSchema(
            section="recoil", key="recovery_rate",
            config_type=ConfigType.FLOAT,
            # 0.0 = kapalı; research_config.ini ve profiller bu değeri kullanıyor
            default=0.1, min_value=0.0, max_value=1.0,
            description="Geri kazanım hızı"
        ),
        
        # Input Ayarları
        "input.movement_type": ConfigSchema(
            section="input", key="movement_type",
            config_type=ConfigType.STRING,
            default="relative",
            choices=["relative", "absolute"],
            description="Fare hareket tipi"
        ),
        "input.speed_x": ConfigSchema(
            section="input", key="speed_x",
            config_type=ConfigType.INTEGER,
            default=350, min_value=100, max_value=1000,
            description="X ekseni hızı (piksel/saniye)"
        ),
        "input.speed_y": ConfigSchema(
            section="input", key="speed_y",
            config_type=ConfigType.INTEGER,
            default=350, min_value=100, max_value=1000,
            description="Y ekseni hızı (piksel/saniye)"
        ),
        "input.y_multiplier": ConfigSchema(
            section="input", key="y_multiplier",
            config_type=ConfigType.FLOAT,
            default=1.0, min_value=0.5, max_value=2.0,
            description="Y ekseni çarpanı"
        ),
        "input.micro_jitter": ConfigSchema(
            section="input", key="micro_jitter",
            config_type=ConfigType.BOOLEAN,
            default=True,
            description="Mikro jitter aktif mi"
        ),
        "input.jitter_amount": ConfigSchema(
            section="input", key="jitter_amount",
            config_type=ConfigType.FLOAT,
            default=0.5, min_value=0.0, max_value=2.0,
            description="Jitter miktarı (piksel)"
        ),
        
        # Kalman Filtresi Ayarları
        "kalman.process_noise": ConfigSchema(
            section="kalman", key="process_noise",
            config_type=ConfigType.FLOAT,
            default=0.01, min_value=0.001, max_value=1.0,
            description="Process noise (Q)"
        ),
        "kalman.measurement_noise": ConfigSchema(
            section="kalman", key="measurement_noise",
            config_type=ConfigType.FLOAT,
            default=0.1, min_value=0.01, max_value=1.0,
            description="Measurement noise (R)"
        ),
        "kalman.max_prediction_frames": ConfigSchema(
            section="kalman", key="max_prediction_frames",
            config_type=ConfigType.INTEGER,
            default=15, min_value=5, max_value=60,
            description="Maksimum tahmin karesi"
        ),
        "kalman.association_threshold": ConfigSchema(
            section="kalman", key="association_threshold",
            config_type=ConfigType.INTEGER,
            default=200, min_value=50, max_value=500,
            description="Veri ilişkilendirme eşiği (piksel)"
        ),
        
        # Oyun Penceresi Kontrolü
        "game.wait_for_window": ConfigSchema(
            section="game", key="wait_for_window",
            config_type=ConfigType.BOOLEAN,
            default=True,
            description="Oyun penceresi öne gelene kadar tespiti duraklat"
        ),
        "game.window_titles": ConfigSchema(
            section="game", key="window_titles",
            config_type=ConfigType.LIST,
            # Gerçek liste olmalı: şema varsayılanı dönüştürülmeden döner,
            # string bırakılırsa tek bir başlık gibi okunur
            default=["VALORANT", "Counter-Strike 2"],
            description="Aranacak pencere başlıkları (virgülle ayrılır, büyük/küçük harf duyarsız)"
        ),
        "game.process_names": ConfigSchema(
            section="game", key="process_names",
            config_type=ConfigType.LIST,
            default=["VALORANT.exe", "VALORANT-Win64-Shipping.exe", "cs2.exe"],
            description="Oyun exe adları - başlıktan güvenilir, birincil tespit yöntemi"
        ),
        "game.use_process_check": ConfigSchema(
            section="game", key="use_process_check",
            config_type=ConfigType.BOOLEAN,
            default=True,
            description="Exe adıyla tespit (kapalıysa yalnızca pencere başlığı kullanılır)"
        ),
        "game.pause_when_closed": ConfigSchema(
            section="game", key="pause_when_closed",
            config_type=ConfigType.BOOLEAN,
            default=True,
            description="Oyun süreci hiç çalışmıyorsa bekleme moduna geç"
        ),

        # Debug Ayarları
        "debug.enabled": ConfigSchema(
            section="debug", key="enabled",
            config_type=ConfigType.BOOLEAN,
            default=False,
            description="Debug modu aktif mi"
        ),
        "debug.show_overlay": ConfigSchema(
            section="debug", key="show_overlay",
            config_type=ConfigType.BOOLEAN,
            default=True,
            description="Görsel overlay göster"
        ),
        "debug.log_level": ConfigSchema(
            section="debug", key="log_level",
            config_type=ConfigType.STRING,
            default="INFO",
            choices=["DEBUG", "INFO", "WARNING", "ERROR"],
            description="Log seviyesi"
        ),
        "debug.save_screenshots": ConfigSchema(
            section="debug", key="save_screenshots",
            config_type=ConfigType.BOOLEAN,
            default=False,
            description="Hata durumunda ekran görüntüsü kaydet"
        ),
        
        # Performans Profili
        "profile.active": ConfigSchema(
            section="profile", key="active",
            config_type=ConfigType.STRING,
            default="balanced",
            choices=["stealth", "balanced", "rapid", "ultra", "custom"],
            description="Aktif performans profili"
        ),
        "profile.description": ConfigSchema(
            section="profile", key="description",
            config_type=ConfigType.STRING,
            default="",
            description="Aktif profilin açıklaması (PerformanceProfiles tarafından yazılır)"
        ),
    }

    # Şema anahtarı -> INI dosyasındaki alternatif seçenek adları.
    # Elle yazılmış research_config.ini bazı ayarları farklı adla tutuyor;
    # şemadaki ad bulunamazsa buradaki adlar denenir (okurken ve yazarken).
    KEY_ALIASES: Dict[str, Tuple[str, ...]] = {
        "recoil.weapon": ("active_weapon",),
    }

    # Dosya başına yazılan açıklama satırları
    FILE_HEADER = (
        "# VisualAim-Research Yapılandırma Dosyası\n"
        "# Bu dosya otomatik olarak yönetilir\n"
        "# Değişikliklerin etkili olması için uygulamayı yeniden başlatın\n\n"
    )

    def __init__(
        self,
        config_path: Union[str, Path] = "research_config.ini",
        profiles_dir: Union[str, Path] = "profiles",
        auto_create: bool = True,
        watch_changes: bool = False
    ):
        """
        ConfigManager başlatıcı
        
        Args:
            config_path: Ana INI dosyası yolu
            profiles_dir: JSON profil dizini
            auto_create: Dosya yoksa otomatik oluştur
            watch_changes: Dosya değişikliklerini izle
        """
        self._lock = threading.RLock()
        self._config_path = Path(config_path).resolve()
        self._profiles_dir = Path(profiles_dir).resolve()
        self._parser = self._new_parser()
        self._cache: Dict[str, Any] = {}
        self._callbacks: List[Tuple[Callable[[str, Any, Any], None], Optional[str]]] = []
        self._last_modified: float = 0.0
        self._watch_changes = watch_changes
        self._watcher_thread: Optional[threading.Thread] = None
        self._stop_watcher = threading.Event()
        
        # Dizinleri oluştur
        self._profiles_dir.mkdir(parents=True, exist_ok=True)
        
        # Dosyayı yükle veya oluştur
        if auto_create and not self._config_path.exists():
            self._create_default_config()
        
        self._load()
        
        # Değişiklik izleyici başlat
        if watch_changes:
            self._start_watcher()
        
        logger.info(f"ConfigManager başlatıldı: {self._config_path}")
    
    @staticmethod
    def _new_parser() -> configparser.ConfigParser:
        """
        Yapılandırma ayrıştırıcısı oluştur

        inline_comment_prefixes KRİTİK: varsayılan olarak configparser satır
        içi yorumları değerin PARÇASI sayar. "head_offset = 0.28  # kafa hizası"
        satırı float('0.28  # kafa hizası') ile çöküyor ve ayar sessizce
        varsayılana dönüyordu - elle yapılan tüm ince ayarlar yok sayılıyordu.
        """
        return configparser.ConfigParser(inline_comment_prefixes=('#', ';'))

    def _create_default_config(self) -> None:
        """Varsayılan yapılandırma dosyası oluştur"""
        # Section'ları grupla
        sections: Dict[str, List[ConfigSchema]] = {}
        for schema in self.DEFAULT_SCHEMA.values():
            if schema.section not in sections:
                sections[schema.section] = []
            sections[schema.section].append(schema)

        # Açıklamalar gerçek yorum satırı olarak yazılır.
        # (Daha önce "; anahtar" adında sahte seçenek olarak parser'a ekleniyordu;
        #  bu sahte anahtarlar bellekteki parser'da kalıp save_profile() çıktısını
        #  ve _parser.items() sonuçlarını kirletiyordu.)
        lines: List[str] = [self.FILE_HEADER.rstrip('\n'), ""]
        for section_name, schemas in sections.items():
            lines.append(f"[{section_name}]")
            for schema in schemas:
                value = self._value_to_string(schema.default, schema.config_type)
                if schema.description:
                    lines.append(f"# {schema.description} (varsayılan: {schema.default})")
                lines.append(f"{schema.key} = {value}")
            lines.append("")

        with open(self._config_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))

        logger.info(f"Varsayılan yapılandırma oluşturuldu: {self._config_path}")

    @synchronized("_lock")
    def _load(self) -> None:
        """Yapılandırmayı dosyadan yükle"""
        if not self._config_path.exists():
            raise ConfigFileNotFoundError(f"Yapılandırma dosyası bulunamadı: {self._config_path}")

        # Temiz parser: read() mevcut parser'ın üzerine ekleme yapar, dosyadan
        # silinen seçenekler bellekte kalırdı (reload() sonrası hayalet değerler).
        parser = self._new_parser()
        parser.read(self._config_path, encoding='utf-8')
        self._parser = parser
        self._last_modified = self._config_path.stat().st_mtime
        self._cache.clear()  # Cache'i temizle

        logger.debug("Yapılandırma yüklendi")
    
    @synchronized("_lock")
    def reload(self) -> bool:
        """
        Yapılandırmayı yeniden yükle
        
        Returns:
            bool: Değişiklik varsa True
        """
        if not self._config_path.exists():
            return False
        
        current_mtime = self._config_path.stat().st_mtime
        if current_mtime == self._last_modified:
            return False
        
        old_values = dict(self._cache)
        self._load()  # cache'i temizler

        # Değişiklik callback'lerini çalıştır.
        # NOT: _load() cache'i boşalttığı için değerler yeniden okunmalı;
        # daha önce boş cache üzerinde dönülüyor ve hiçbir callback tetiklenmiyordu.
        for key, old_value in old_values.items():
            try:
                new_value = self.get(key, old_value)
            except ConfigError:
                continue
            if old_value != new_value:
                self._notify_change(key, old_value, new_value)

        logger.info("Yapılandırma yeniden yüklendi")
        return True
    
    def _get_schema(self, key: str) -> Optional[ConfigSchema]:
        """Şema tanımını al"""
        return self.DEFAULT_SCHEMA.get(key)
    
    def _convert_value(
        self,
        value: str,
        config_type: ConfigType,
        schema: Optional[ConfigSchema] = None
    ) -> Any:
        """
        String değeri hedef tipe dönüştür
        
        Args:
            value: Ham string değer
            config_type: Hedef tip
            schema: Validasyon için şema (opsiyonel)
        
        Returns:
            Dönüştürülmüş değer
        """
        try:
            if config_type == ConfigType.STRING:
                return value
            
            elif config_type == ConfigType.INTEGER:
                result = int(value)
                if schema:
                    if schema.min_value is not None and result < schema.min_value:
                        result = int(schema.min_value)
                    if schema.max_value is not None and result > schema.max_value:
                        result = int(schema.max_value)
                return result
            
            elif config_type == ConfigType.FLOAT:
                result = float(value)
                if schema:
                    if schema.min_value is not None and result < schema.min_value:
                        result = float(schema.min_value)
                    if schema.max_value is not None and result > schema.max_value:
                        result = float(schema.max_value)
                return result
            
            elif config_type == ConfigType.BOOLEAN:
                return value.lower() in ('true', '1', 'yes', 'on', 'enabled')
            
            elif config_type == ConfigType.LIST:
                # Virgülle ayrılmış değerler
                items = [item.strip() for item in value.split(',')]
                # Tip çıkarımı
                if all(item.isdigit() for item in items):
                    return [int(item) for item in items]
                try:
                    return [float(item) for item in items]
                except ValueError:
                    return items
            
            elif config_type == ConfigType.TUPLE:
                # Liste olarak parse et, tuple'a çevir
                return tuple(self._convert_value(value, ConfigType.LIST))
            
            elif config_type == ConfigType.PATH:
                return Path(value).expanduser().resolve()
            
            elif config_type == ConfigType.COLOR_RANGE:
                # HSV renk aralığı: "0,150,150" -> (0, 150, 150)
                values = [int(v.strip()) for v in value.split(',')]
                if len(values) != 3:
                    raise ValueError("Renk aralığı 3 değer içermeli (H,S,V)")
                return tuple(values)
            
            else:
                return value
        
        except (ValueError, TypeError) as e:
            logger.warning(f"Değer dönüştürme hatası ({value} -> {config_type}): {e}")
            if schema:
                return schema.default
            raise
    
    def _value_to_string(self, value: Any, config_type: ConfigType) -> str:
        """Değeri string'e dönüştür"""
        if config_type in (ConfigType.LIST, ConfigType.TUPLE, ConfigType.COLOR_RANGE):
            if isinstance(value, (list, tuple)):
                return ', '.join(str(v) for v in value)
        return str(value)
    
    def _read_raw(self, key: str) -> Optional[str]:
        """
        INI dosyasından ham string değeri oku (KEY_ALIASES dahil).

        Returns:
            Ham değer, bulunamazsa None
        """
        if '.' not in key:
            raise ConfigError(f"Anahtar 'section.key' biçiminde olmalı: {key}")

        section, option = key.split('.', 1)
        if self._parser.has_option(section, option):
            return self._parser.get(section, option)

        for alias in self.KEY_ALIASES.get(key, ()):
            if self._parser.has_option(section, alias):
                return self._parser.get(section, alias)

        return None

    @staticmethod
    def _auto_convert(raw: str) -> Any:
        """Şemasız anahtarlar için ham string'den tip çıkarımı yap"""
        text = raw.strip()
        lowered = text.lower()

        if lowered in ('true', 'yes', 'on', 'enabled'):
            return True
        if lowered in ('false', 'no', 'off', 'disabled'):
            return False

        try:
            return int(text)
        except ValueError:
            pass
        try:
            return float(text)
        except ValueError:
            pass

        return text

    @synchronized("_lock")
    def get(self, key: str, fallback: Any = None) -> Any:
        """
        Yapılandırma değeri al (cache'li)

        Args:
            key: "section.key" formatında anahtar
            fallback: Varsayılan değer (şema yoksa kullanılır)

        Returns:
            Tip dönüştürülmüş değer
        """
        # Cache kontrolü
        if key in self._cache:
            return self._cache[key]

        schema = self._get_schema(key)

        # Değeri oku
        try:
            raw_value = self._read_raw(key)

            if raw_value is not None:
                if schema is not None:
                    value = self._convert_value(raw_value, schema.config_type, schema)
                    # Seçenek dışı değerler varsayılana düşürülür; aksi halde
                    # main.py'deki TriggerMode(...)/WeaponType(...) gibi enum
                    # dönüşümleri ValueError ile çöker.
                    if schema.choices and value not in schema.choices:
                        logger.warning(
                            f"Geçersiz değer ({key} = {value}), varsayılana dönülüyor: "
                            f"{schema.default} (seçenekler: {schema.choices})"
                        )
                        value = schema.default
                elif fallback is not None:
                    # Şema yok ama beklenen tip fallback'ten çıkarılabiliyor
                    value = self._convert_value(raw_value, self._infer_type(fallback))
                else:
                    # Şemada olmayan ama dosyada bulunan anahtar
                    # (ör. [hotkeys], [screen], [general] bölümleri)
                    value = self._auto_convert(raw_value)
            else:
                # Ne dosyada ne de şemada varsa bu bir yazım hatasıdır
                if schema is None and fallback is None:
                    raise ConfigError(f"Bilinmeyen yapılandırma anahtarı: {key}")
                value = schema.default if schema else fallback

            # Cache'e al
            self._cache[key] = value
            return value

        except ConfigError:
            raise
        except Exception as e:
            logger.error(f"Yapılandırma okuma hatası ({key}): {e}")
            return schema.default if schema else fallback

    def _get_safe(self, key: str) -> Any:
        """get() ile aynı, ancak bilinmeyen anahtar için hata yerine None döner"""
        try:
            return self.get(key)
        except ConfigError:
            return None

    def _infer_type(self, value: Any) -> ConfigType:
        """Değerden tip çıkarımı yap"""
        if isinstance(value, bool):
            return ConfigType.BOOLEAN
        elif isinstance(value, int):
            return ConfigType.INTEGER
        elif isinstance(value, float):
            return ConfigType.FLOAT
        elif isinstance(value, (list, tuple)):
            return ConfigType.LIST
        elif isinstance(value, Path):
            return ConfigType.PATH
        return ConfigType.STRING
    
    @synchronized("_lock")
    def set(self, key: str, value: Any, save: bool = True) -> bool:
        """
        Yapılandırma değeri ayarla
        
        Args:
            key: "section.key" formatında anahtar
            value: Yeni değer
            save: Hemen dosyaya kaydet
        
        Returns:
            bool: Başarılı ise True
        """
        schema = self._get_schema(key)
        
        # Validasyon
        if schema:
            if schema.choices and value not in schema.choices:
                logger.error(f"Geçersiz değer: {value}. Seçenekler: {schema.choices}")
                return False
            
            if isinstance(value, (int, float)):
                if schema.min_value is not None and value < schema.min_value:
                    value = schema.min_value
                if schema.max_value is not None and value > schema.max_value:
                    value = schema.max_value
        
        try:
            if '.' not in key:
                raise ConfigError(f"Anahtar 'section.key' biçiminde olmalı: {key}")

            section, option = key.split('.', 1)

            # Section yoksa oluştur
            if not self._parser.has_section(section):
                self._parser.add_section(section)

            # Dosyada alternatif adla duruyorsa (bkz. KEY_ALIASES) aynı seçeneği
            # güncelle; aksi halde çift kayıt oluşur ve okuma yine eskisini bulur.
            if not self._parser.has_option(section, option):
                for alias in self.KEY_ALIASES.get(key, ()):
                    if self._parser.has_option(section, alias):
                        option = alias
                        break

            # Değeri yaz
            config_type = schema.config_type if schema else self._infer_type(value)
            str_value = self._value_to_string(value, config_type)
            # Şemasız/yeni anahtarlarda get() hata fırlatabilir; set() bu yüzden
            # şema dışındaki her anahtar için başarısız oluyordu.
            old_value = self._get_safe(key)

            self._parser.set(section, option, str_value)
            self._cache[key] = value
            
            # Callback'leri çalıştır
            if old_value != value:
                self._notify_change(key, old_value, value)
            
            # Kaydet
            if save:
                self.save()
            
            logger.debug(f"Yapılandırma güncellendi: {key} = {value}")
            return True
            
        except Exception as e:
            logger.error(f"Yapılandırma yazma hatası ({key}): {e}")
            return False
    
    @synchronized("_lock")
    def save(self) -> None:
        """Yapılandırmayı dosyaya kaydet"""
        with open(self._config_path, 'w', encoding='utf-8') as f:
            f.write(self.FILE_HEADER)
            self._parser.write(f)
        self._last_modified = self._config_path.stat().st_mtime
        logger.debug("Yapılandırma kaydedildi")
    
    def get_color_range(self, prefix: str = "") -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
        """
        HSV renk aralığı al (detector için shortcut)
        
        Args:
            prefix: "2" gibi ikinci aralık için
        
        Returns:
            ((lower_h, lower_s, lower_v), (upper_h, upper_s, upper_v))
        """
        suffix = "2" if prefix == "2" else ""
        lower = (
            self.get(f"color.lower_h{suffix}"),
            self.get(f"color.lower_s{suffix}"),
            self.get(f"color.lower_v{suffix}")
        )
        upper = (
            self.get(f"color.upper_h{suffix}"),
            self.get(f"color.upper_s{suffix}"),
            self.get(f"color.upper_v{suffix}")
        )
        return lower, upper
    
    def get_section(self, section: str) -> Dict[str, Any]:
        """
        Tüm section değerlerini dict olarak al
        
        Args:
            section: Section adı
        
        Returns:
            {key: value} sözlüğü
        """
        result = {}
        for full_key, schema in self.DEFAULT_SCHEMA.items():
            if full_key.startswith(f"{section}."):
                key = full_key.split('.', 1)[1]
                result[key] = self.get(full_key)
        return result
    
    # JSON Profil Yönetimi
    
    def load_profile(self, profile_name: str) -> bool:
        """
        JSON profil yükle ve mevcut yapılandırmayı güncelle
        
        Args:
            profile_name: Profil adı (".json" olmadan)
        
        Returns:
            bool: Başarılı ise True
        """
        profile_path = self._profiles_dir / f"{profile_name}.json"
        
        if not profile_path.exists():
            logger.error(f"Profil bulunamadı: {profile_path}")
            return False
        
        try:
            with open(profile_path, 'r', encoding='utf-8') as f:
                profile_data = json.load(f)

            # Profil değerlerini PROFILE_KEY_MAP üzerinden şema anahtarlarına çevir
            applied = 0
            skipped: List[str] = []

            for json_path, config_key in PROFILE_KEY_MAP:
                found, value = _resolve_json_path(profile_data, json_path)
                if not found:
                    continue

                # Gerekiyorsa değeri şema seçeneğine çevir
                value = PROFILE_VALUE_MAP.get(config_key, {}).get(value, value)

                if self.set(config_key, value, save=False):
                    applied += 1
                else:
                    skipped.append(f"{json_path} -> {config_key}")

            # Eski biçim profiller (2.0.0 öncesi save_profile düz INI bölümleri
            # döküyordu: {"aim": {"speed": "0.35"}, ...}). Bu dosyalar
            # PROFILE_KEY_MAP ile eşleşmiyor; doğrudan section.key olarak
            # uygulanır ki eski kayıtlar kullanılabilir kalsın.
            for section, values in profile_data.items():
                if section.startswith('_') or not isinstance(values, dict):
                    continue
                for option, raw in values.items():
                    config_key = f"{section}.{option}"
                    if config_key not in self.DEFAULT_SCHEMA or isinstance(raw, (dict, list)):
                        continue
                    schema = self.DEFAULT_SCHEMA[config_key]
                    parsed = (self._convert_value(str(raw), schema.config_type, schema)
                              if isinstance(raw, str) else raw)
                    if self.set(config_key, parsed, save=False):
                        applied += 1

            # Profilde tek renk aralığı varsa ikincil aralığı birincinin kopyası yap.
            # ColorDetector iki maskeyi her zaman OR'lar (core/detector.py:173);
            # ikincil aralık bir önceki profilden kalırsa istenmeyen renkler de
            # hedef sayılır (ör. CS2 morunu ararken Valorant kırmızısı).
            found_ranges, ranges = _resolve_json_path(profile_data, "color_detection.ranges")
            if found_ranges and isinstance(ranges, list) and len(ranges) == 1:
                for bound in ("lower", "upper"):
                    for idx, comp in enumerate(("h", "s", "v")):
                        found, value = _resolve_json_path(
                            profile_data, f"color_detection.ranges.0.{bound}.{idx}"
                        )
                        if found and self.set(f"color.{bound}_{comp}2", value, save=False):
                            applied += 1

            if applied == 0:
                logger.error(
                    f"Profil uygulanamadı: {profile_name} "
                    "(hiçbir anahtar eşleşmedi - dosya yapısı beklenenden farklı)"
                )
                return False

            self.save()

            # profile.active performans profilini tutar (stealth/balanced/...),
            # oyun profili adı bu listede yoksa "custom" olarak işaretlenir.
            active_schema = self._get_schema("profile.active")
            if active_schema and active_schema.choices and profile_name not in active_schema.choices:
                self.set("profile.active", "custom")
            else:
                self.set("profile.active", profile_name)

            if skipped:
                logger.warning(
                    f"Profil '{profile_name}': {len(skipped)} anahtar atlandı "
                    f"({', '.join(skipped[:3])}{'...' if len(skipped) > 3 else ''})"
                )

            logger.info(f"Profil yüklendi: {profile_name} ({applied} ayar uygulandı)")
            return True

        except Exception as e:
            logger.error(f"Profil yükleme hatası: {e}")
            return False
    
    def save_profile(self, profile_name: str, description: str = "") -> bool:
        """
        Mevcut yapılandırmayı JSON profili olarak kaydet

        Çıktı, load_profile()'ın beklediği iç içe profil biçimindedir
        (PROFILE_KEY_MAP ters yönde uygulanır). Daha önce section'lar düz
        olarak dökülüyordu ve kaydedilen profil geri yüklenemiyordu.

        Args:
            profile_name: Profil adı
            description: Profil açıklaması

        Returns:
            bool: Başarılı ise True
        """
        profile_path = self._profiles_dir / f"{profile_name}.json"

        profile_data: Dict[str, Any] = {
            "profile_name": profile_name,
            "description": description,
            "version": "2.0.0",
            "_meta": {
                "name": profile_name,
                "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "source": "ConfigManager.save_profile",
            },
        }

        # Şema değerlerini profil yoluna yaz
        written: set = set()
        for json_path, config_key in PROFILE_KEY_MAP:
            # Aynı JSON yoluna birden fazla anahtar eşlenmiş olabilir
            # (ör. compensation.strength <- compensation_x ve _y); ilki yazılır.
            if json_path in written:
                continue

            value = self._get_safe(config_key)
            if value is None:
                continue

            # Ters çevrim yalnızca hashlenebilir değerler için (liste/tuple
            # tipindeki ayarlar - ör. game.window_titles - sözlük anahtarı olamaz)
            if isinstance(value, (str, int, float, bool)):
                value = PROFILE_VALUE_MAP_REVERSE.get(config_key, {}).get(value, value)
            elif isinstance(value, tuple):
                value = list(value)

            _set_json_path(profile_data, json_path, value)
            written.add(json_path)

        # Şemada karşılığı olmayan bölümler (weapon_*, hotkeys, screen ...)
        # kaybolmasın diye ham INI görüntüsü de saklanır.
        profile_data["_ini_snapshot"] = {
            section: dict(self._parser.items(section))
            for section in self._parser.sections()
        }

        try:
            with open(profile_path, 'w', encoding='utf-8') as f:
                json.dump(profile_data, f, indent=4, ensure_ascii=False)
            
            logger.info(f"Profil kaydedildi: {profile_path}")
            return True
            
        except Exception as e:
            logger.error(f"Profil kaydetme hatası: {e}")
            return False
    
    def list_profiles(self) -> List[str]:
        """
        Mevcut profilleri listele
        
        Returns:
            Profil adları listesi
        """
        profiles = []
        for file in self._profiles_dir.glob("*.json"):
            profiles.append(file.stem)
        return sorted(profiles)
    
    def delete_profile(self, profile_name: str) -> bool:
        """
        Profil sil
        
        Args:
            profile_name: Silinecek profil adı
        
        Returns:
            bool: Başarılı ise True
        """
        profile_path = self._profiles_dir / f"{profile_name}.json"
        
        if not profile_path.exists():
            return False
        
        try:
            profile_path.unlink()
            logger.info(f"Profil silindi: {profile_name}")
            return True
        except Exception as e:
            logger.error(f"Profil silme hatası: {e}")
            return False
    
    # Değişiklik İzleme
    
    def register_callback(
        self,
        callback: Callable[[str, Any, Any], None],
        key_filter: Optional[str] = None
    ) -> None:
        """
        Yapılandırma değişikliği callback'i kaydet
        
        Args:
            callback: Fonksiyon(key, old_value, new_value)
            key_filter: Sadece bu anahtar için (None = tümü)
        """
        self._callbacks.append((callback, key_filter))
    
    def _notify_change(self, key: str, old_value: Any, new_value: Any) -> None:
        """Değişiklik bildirimi gönder"""
        for callback, key_filter in self._callbacks:
            if key_filter is None or key == key_filter:
                try:
                    callback(key, old_value, new_value)
                except Exception as e:
                    logger.error(f"Callback hatası: {e}")
    
    def _start_watcher(self) -> None:
        """Dosya değişiklik izleyicisini başlat"""
        def watcher():
            # wait(): sleep yerine olay bekler, stop_watcher() anında etkili olur
            while not self._stop_watcher.wait(1.0):
                try:
                    if self._config_path.exists():
                        current_mtime = self._config_path.stat().st_mtime
                        if current_mtime != self._last_modified:
                            self.reload()
                except Exception as e:
                    logger.error(f"İzleyici hatası: {e}")

        self._watcher_thread = threading.Thread(target=watcher, daemon=True)
        self._watcher_thread.start()
        logger.info("Yapılandırma izleyici başlatıldı")
    
    def stop_watcher(self) -> None:
        """İzleyiciyi durdur"""
        self._stop_watcher.set()
        if self._watcher_thread:
            self._watcher_thread.join(timeout=2)
    
    # Context Manager
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_watcher()
        return False
    
    def __repr__(self) -> str:
        return f"ConfigManager({self._config_path}, profiles={len(self.list_profiles())})"


# Test bloğu
if __name__ == "__main__":
    import sys
    import tempfile
    import shutil

    # Windows konsol kodlaması (Türkçe karakterler cp1252'de çöküyordu)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # Test dizini oluştur
    test_dir = tempfile.mkdtemp()
    config_path = Path(test_dir) / "test_config.ini"
    profiles_dir = Path(test_dir) / "profiles"
    
    try:
        print("=" * 60)
        print("CONFIG MANAGER TEST")
        print("=" * 60)
        
        # ConfigManager oluştur
        config = ConfigManager(
            config_path=config_path,
            profiles_dir=profiles_dir,
            auto_create=True,
            watch_changes=False
        )
        
        # Temel okuma testleri
        print("\n[TEST] Temel Değer Okuma")
        print(f"capture.backend: {config.get('capture.backend')}")
        print(f"aim.speed: {config.get('aim.speed')}")
        print(f"color.lower_h: {config.get('color.lower_h')}")
        
        # Renk aralığı testi
        print("\n[TEST] Renk Aralığı")
        lower, upper = config.get_color_range()
        print(f"Lower: {lower}")
        print(f"Upper: {upper}")
        
        # Değer değiştirme testi
        print("\n[TEST] Değer Değiştirme")
        config.set("aim.speed", 0.75)
        print(f"Yeni aim.speed: {config.get('aim.speed')}")
        
        # Validasyon testi (limit dışı değer)
        print("\n[TEST] Validasyon")
        config.set("aim.speed", 5.0)  # Max 1.0, clamp edilecek
        print(f"Clamped aim.speed: {config.get('aim.speed')}")
        
        # Section okuma
        print("\n[TEST] Section Okuma")
        aim_settings = config.get_section("aim")
        print(f"Aim ayarları: {aim_settings}")
        
        # Profil kaydetme
        print("\n[TEST] Profil Kaydetme")
        config.save_profile("test_profile", "Test profili")
        print(f"Profiller: {config.list_profiles()}")
        
        # Profil yükleme (kaydet -> yükle turu geri dönmeli)
        print("\n[TEST] Profil Yükleme")
        saved_speed = config.get("aim.speed")
        config.set("aim.speed", 0.1)  # Değiştir
        print(f"Önce: {config.get('aim.speed')}")
        config.load_profile("test_profile")
        print(f"Sonra: {config.get('aim.speed')}")
        assert config.get("aim.speed") == saved_speed, "Profil turu geri yüklenmedi"

        # Şemada olmayan ama INI'de bulunan anahtar okunabilmeli
        print("\n[TEST] Şemasız Anahtar")
        config.set("hotkeys.aim_toggle", "F2")
        print(f"hotkeys.aim_toggle: {config.get('hotkeys.aim_toggle')}")

        # Callback testi
        print("\n[TEST] Callback")
        def on_change(key, old, new):
            print(f"  Değişiklik: {key} = {old} -> {new}")

        config.register_callback(on_change)
        config.set("trigger.enabled", False)

        # reload() callback testi (dosya dışarıdan değiştirildiğinde)
        print("\n[TEST] Reload Bildirimi")
        text = config_path.read_text(encoding="utf-8")
        config_path.write_text(text.replace("fov_radius = 250", "fov_radius = 300"), encoding="utf-8")
        config._last_modified = 0.0  # mtime çözünürlüğünü atla
        print(f"reload: {config.reload()}")
        print(f"aim.fov_radius: {config.get('aim.fov_radius')}")

        print("\n" + "=" * 60)
        print("TÜM TESTLER BAŞARILI")
        print("=" * 60)
        
    finally:
        # Temizlik
        shutil.rmtree(test_dir, ignore_errors=True)
        print(f"\nTemizlendi: {test_dir}") 
