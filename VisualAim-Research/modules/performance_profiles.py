#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VisualAim-Research: Performance Profiles Module
==============================================
Sistem performans profilleri yönetimi.

Profiller:
- STEALTH: 30 FPS, düşük CPU, insansı gecikmeler (güvenli mod)
- BALANCED: 60 FPS, orta CPU, dengeli (önerilen)
- RAPID: 90 FPS, yüksek CPU, hızlı tepki
- ULTRA: 144 FPS, maksimum CPU, anlık snap

Yazar: İhsan
Versiyon: 1.0.0
"""

import sys
import time
import threading
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

# UI modüllerinden import
try:
    from ui.logger import get_logger, Colors
    from ui.config_manager import ConfigManager
except ImportError:
    # Standalone çalışma için
    import logging
    get_logger = None
    Colors = None


class ProfileType(Enum):
    """Performans profili tipleri"""
    STEALTH = "stealth"      # Güvenli, yavaş, insansı
    BALANCED = "balanced"    # Dengeli, önerilen
    RAPID = "rapid"          # Hızlı, agresif
    ULTRA = "ultra"          # Maksimum performans
    CUSTOM = "custom"        # Kullanıcı tanımlı


@dataclass
class ProfileSettings:
    """Profil ayarları veri yapısı"""
    name: str
    target_fps: int
    capture_fps: int
    aim_speed: float
    aim_smoothing: float
    trigger_delay_min: int  # ms
    trigger_delay_max: int  # ms
    move_steps_min: int
    move_steps_max: int
    jitter_amount: float
    cpu_priority: str  # "low", "normal", "high", "realtime"
    description: str


class PerformanceProfiles:
    """
    Performans profili yöneticisi
    
    Intel Iris Xe grafik kartı için optimize edilmiş profiller.
    Her profil CPU kullanımı, FPS ve insansı davranış arasında
    farklı dengelemeler sunar.
    
    Attributes:
        current_profile: Aktif profil tipi
        settings: Mevcut profil ayarları
        original_settings: Varsayılan ayarlar (reset için)
    """
    
    # Profil tanımları
    PROFILE_DEFINITIONS: Dict[ProfileType, ProfileSettings] = {
        ProfileType.STEALTH: ProfileSettings(
            name="STEALTH",
            target_fps=30,
            capture_fps=30,
            aim_speed=0.20,
            aim_smoothing=0.40,
            trigger_delay_min=50,
            trigger_delay_max=150,
            move_steps_min=8,
            move_steps_max=20,
            jitter_amount=1.5,
            cpu_priority="low",
            description="Güvenli mod - düşük CPU, yüksek gecikme, insansı"
        ),
        ProfileType.BALANCED: ProfileSettings(
            name="BALANCED",
            target_fps=60,
            capture_fps=60,
            aim_speed=0.35,
            aim_smoothing=0.15,
            trigger_delay_min=20,
            trigger_delay_max=60,
            move_steps_min=5,
            move_steps_max=12,
            jitter_amount=1.0,
            cpu_priority="normal",
            description="Dengeli mod - orta CPU, önerilen ayarlar"
        ),
        ProfileType.RAPID: ProfileSettings(
            name="RAPID",
            target_fps=90,
            capture_fps=90,
            aim_speed=0.60,
            aim_smoothing=0.05,
            trigger_delay_min=10,
            trigger_delay_max=35,
            move_steps_min=3,
            move_steps_max=8,
            jitter_amount=0.5,
            cpu_priority="high",
            description="Hızlı mod - yüksek CPU, hızlı tepki"
        ),
        ProfileType.ULTRA: ProfileSettings(
            name="ULTRA",
            target_fps=144,
            capture_fps=144,
            aim_speed=0.90,
            aim_smoothing=0.0,
            trigger_delay_min=5,
            trigger_delay_max=20,
            move_steps_min=2,
            move_steps_max=5,
            jitter_amount=0.2,
            cpu_priority="high",
            description="Ultra mod - maksimum CPU, anlık snap"
        ),
        ProfileType.CUSTOM: ProfileSettings(
            name="CUSTOM",
            target_fps=60,
            capture_fps=60,
            aim_speed=0.35,
            aim_smoothing=0.15,
            trigger_delay_min=20,
            trigger_delay_max=60,
            move_steps_min=5,
            move_steps_max=12,
            jitter_amount=1.0,
            cpu_priority="normal",
            description="Kullanıcı tanımlı özel ayarlar"
        )
    }
    
    def __init__(self, logger=None):
        self.logger = logger or (get_logger() if get_logger else None)
        self.current_profile = ProfileType.BALANCED
        self._lock = threading.RLock()
        self._callbacks: list = []
        
        # Intel Iris Xe kontrolü
        self._is_intel_iris = self._detect_intel_iris()
        
        if self.logger:
            self.logger.info(f"[PerformanceProfiles] Initialized")
            if self._is_intel_iris:
                self.logger.info("[PerformanceProfiles] Intel Iris Xe detected - optimized profiles loaded")
    
    def _detect_intel_iris(self) -> bool:
        """Intel Iris Xe grafik kartı tespiti"""
        try:
            import subprocess
            result = subprocess.run(
                ["wmic", "path", "win32_VideoController", "get", "name"],
                capture_output=True, text=True, timeout=5
            )
            output = result.stdout.lower()
            return "iris" in output or "intel" in output
        except:
            return False
    
    def apply_profile(self, profile_name: str, config_dict: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Profil uygula ve config sözlüğünü güncelle
        
        Args:
            profile_name: "stealth", "balanced", "rapid", "ultra", "custom"
            config_dict: Güncellenecek config sözlüğü (None ise yeni dict döndürür)
        
        Returns:
            Güncellenmiş config sözlüğü
        """
        with self._lock:
            try:
                profile_type = ProfileType(profile_name.lower())
            except ValueError:
                if self.logger:
                    self.logger.warning(f"[PerformanceProfiles] Unknown profile: {profile_name}, using BALANCED")
                profile_type = ProfileType.BALANCED
            
            settings = self.PROFILE_DEFINITIONS[profile_type]
            self.current_profile = profile_type
            
            # Config sözlüğü oluştur/güncelle
            if config_dict is None:
                config_dict = {}
            
            # Capture ayarları
            config_dict['capture.target_fps'] = settings.capture_fps
            
            # Aim ayarları
            config_dict['aim.speed'] = settings.aim_speed
            config_dict['aim.smoothing'] = settings.aim_smoothing
            
            # Trigger ayarları
            config_dict['trigger.delay_min'] = settings.trigger_delay_min
            config_dict['trigger.delay_max'] = settings.trigger_delay_max
            
            # Input ayarları (insansı hareket için)
            config_dict['input.micro_jitter'] = True
            config_dict['input.jitter_amount'] = settings.jitter_amount
            
            # Profil metadata
            config_dict['profile.active'] = settings.name.lower()
            config_dict['profile.description'] = settings.description
            
            # Callback'leri çalıştır
            for callback in self._callbacks:
                try:
                    callback(profile_type, settings)
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"[PerformanceProfiles] Callback error: {e}")
            
            if self.logger:
                self.logger.success(f"[PerformanceProfiles] Profile applied: {settings.name}")
                self.logger.info(f"[PerformanceProfiles] Target FPS: {settings.target_fps}, "
                               f"Aim Speed: {settings.aim_speed:.2f}, "
                               f"Jitter: {settings.jitter_amount:.1f}px")
            
            return config_dict
    
    def get_current_settings(self) -> ProfileSettings:
        """Mevcut profil ayarlarını al"""
        with self._lock:
            return self.PROFILE_DEFINITIONS[self.current_profile]
    
    def get_profile_names(self) -> list:
        """Mevcut profil isimlerini listele"""
        return [p.value for p in ProfileType]
    
    def register_callback(self, callback: Callable[[ProfileType, ProfileSettings], None]) -> None:
        """
        Profil değişikliği callback'i kaydet
        
        Args:
            callback: Fonksiyon(profile_type, settings)
        """
        self._callbacks.append(callback)
    
    def optimize_for_intel_iris(self, config_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Intel Iris Xe için özel optimizasyonlar uygula
        
        Args:
            config_dict: Güncellenecek config
        
        Returns:
            Optimizasyon uygulanmış config
        """
        if not self._is_intel_iris:
            return config_dict
        
        # Intel Iris Xe optimizasyonları
        # NOT: capture.backend BİLEREK ayarlanmıyor - backend seçimi kullanıcıya
        # ait (config'te capture.backend). Buradan zorlanırsa elle seçilen
        # backend her açılışta eziliyordu.
        config_dict['capture.show_fps'] = False   # Gereksiz CPU kullanımını azalt
        config_dict['color.min_blob_size'] = 40   # Daha az noise, daha hızlı işleme
        
        if self.logger:
            self.logger.info("[PerformanceProfiles] Intel Iris Xe optimizations applied")
        
        return config_dict
    
    def estimate_cpu_usage(self) -> str:
        """Tahmini CPU kullanımını döndür"""
        settings = self.get_current_settings()
        fps_factor = settings.target_fps / 60.0
        speed_factor = settings.aim_speed
        
        estimated = fps_factor * speed_factor * 25  # Baz tahmin
        
        if estimated < 15:
            return "Düşük (~10-15%)"
        elif estimated < 30:
            return "Orta (~20-30%)"
        elif estimated < 50:
            return "Yüksek (~40-50%)"
        else:
            return "Çok Yüksek (~60%+)"


# Test bloğu
if __name__ == "__main__":
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except:
            pass
    
    print("=" * 70)
    print("Performance Profiles Test")
    print("=" * 70)
    
    profiles = PerformanceProfiles()
    
    print("\n[Mevcut Profiller]")
    for name in profiles.get_profile_names():
        settings = profiles.PROFILE_DEFINITIONS[ProfileType(name)]
        print(f"  • {settings.name}: {settings.description}")
        print(f"    FPS: {settings.target_fps}, Speed: {settings.aim_speed}, "
              f"Jitter: {settings.jitter_amount}px")
    
    print(f"\n[Intel Iris Xe Tespiti]: {'Evet' if profiles._is_intel_iris else 'Hayır'}")
    
    # Profil uygulama testi
    print("\n[Profil Uygulama Testi]")
    for profile_name in ["stealth", "balanced", "rapid", "ultra"]:
        config = {}
        result = profiles.apply_profile(profile_name, config)
        print(f"\n{profile_name.upper()}:")
        for key in ['capture.target_fps', 'aim.speed', 'trigger.delay_min']:
            print(f"  {key}: {result.get(key)}")
    
    print("\n" + "=" * 70)
    print("Test tamamlandı") 
