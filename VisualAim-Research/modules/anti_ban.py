#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VisualAim-Research: Anti-Ban / Humanizer Module
==============================================
İnsan benzeri davranış simülasyonu ve anti-tespit önlemleri.

Özellikler:
- Fare hareketine rastgele sapma (jitter) ekleme
- Tıklamalara rastgele gecikme ekleme (50-150ms)
- Hareket eğrisi varyasyonu
- Reaksiyon zamanı rastgeleleştirme

Yazar: İhsan
Versiyon: 1.0.0
"""

import random
import time
import math
import threading
from typing import Tuple, Optional, Dict, Any
from dataclasses import dataclass

# Core modüllerden import
try:
    from core.input_controller import InputController, MoveType
    from ui.logger import get_logger
except ImportError:
    # Standalone çalışma için
    InputController = None
    MoveType = None
    get_logger = None


@dataclass
class HumanizationParams:
    """İnsan benzeri davranış parametreleri"""
    jitter_enabled: bool = True
    jitter_min: float = 1.0      # piksel
    jitter_max: float = 5.0      # piksel
    
    delay_enabled: bool = True
    delay_min_ms: int = 50       # milisaniye
    delay_max_ms: int = 150      # milisaniye
    
    curve_variation: float = 0.05  # Bezier kontrol noktası varyasyonu (%)
    reaction_delay_enabled: bool = True
    reaction_min_ms: int = 80
    reaction_max_ms: int = 200


class Humanizer:
    """
    İnsan benzeri davranış üretici
    
    Amaç: Hedef tespit ve nişan alma süreçlerinde mekanik/robotik
    davranışları önleyerek daha insansı hareketler üretmek.
    
    Akademik Not: Bu modül, görsel tabanlı sistemlerin davranışsal
    analizlerde nasıl tespit edilebileceğini gösteren savunma
    araştırması kapsamında geliştirilmiştir.
    """
    
    def __init__(self, params: Optional[HumanizationParams] = None, logger=None):
        self.params = params or HumanizationParams()
        self.logger = logger or (get_logger() if get_logger else None)
        self._lock = threading.RLock()
        self._last_action_time = 0.0
        
        # İstatistikler
        self.stats = {
            'jitter_applied': 0,
            'delays_added': 0,
            'total_jitter_pixels': 0.0,
            'total_delay_ms': 0.0
        }
        
        if self.logger:
            self.logger.info("[Humanizer] Initialized")
            self.logger.info(f"[Humanizer] Jitter: {self.params.jitter_min}-{self.params.jitter_max}px, "
                           f"Delay: {self.params.delay_min_ms}-{self.params.delay_max_ms}ms")
    
    def apply_humanization(self, dx: float, dy: float) -> Tuple[float, float]:
        """
        Fare hareketine insansı sapma (jitter) ekle
        
        Args:
            dx: Orijinal X delta (piksel)
            dy: Orijinal Y delta (piksel)
        
        Returns:
            (adjusted_dx, adjusted_dy): Düzeltilmiş hareket değerleri
        """
        with self._lock:
            if not self.params.jitter_enabled:
                return dx, dy
            
            # Mesafe hesapla (büyük hareketlerde daha az jitter)
            distance = math.sqrt(dx * dx + dy * dy)
            
            # Mesafeye göre jitter ölçeği (yakın hedeflerde daha fazla jitter)
            if distance < 50:
                scale = 1.0
            elif distance < 150:
                scale = 0.7
            else:
                scale = 0.4
            
            # Rastgele jitter üret
            jitter_x = random.uniform(-self.params.jitter_max, self.params.jitter_max) * scale
            jitter_y = random.uniform(-self.params.jitter_max, self.params.jitter_max) * scale
            
            # Minimum jitter kontrolü
            if abs(jitter_x) < self.params.jitter_min:
                jitter_x = 0
            if abs(jitter_y) < self.params.jitter_min:
                jitter_y = 0
            
            # İstatistik güncelle
            self.stats['jitter_applied'] += 1
            self.stats['total_jitter_pixels'] += abs(jitter_x) + abs(jitter_y)
            
            return dx + jitter_x, dy + jitter_y
    
    def get_click_delay(self) -> float:
        """
        Tıklama için rastgele gecikme süresi al (saniye)
        
        Returns:
            Gecikme süresi (saniye cinsinden)
        """
        with self._lock:
            if not self.params.delay_enabled:
                return 0.0
            
            delay_ms = random.randint(self.params.delay_min_ms, self.params.delay_max_ms)
            self.stats['delays_added'] += 1
            self.stats['total_delay_ms'] += delay_ms
            
            return delay_ms / 1000.0
    
    def get_reaction_delay(self) -> float:
        """
        Hedef tespiti sonrası reaksiyon gecikmesi (saniye)
        
        İnsanların hedefi gördükten sonra tepki verme süresini simüle eder.
        80-200ms arası rastgele değer döndürür.
        
        Returns:
            Reaksiyon gecikmesi (saniye)
        """
        if not self.params.reaction_delay_enabled:
            return 0.0
        
        delay_ms = random.randint(self.params.reaction_min_ms, self.params.reaction_max_ms)
        return delay_ms / 1000.0
    
    def vary_bezier_control(self, base_control: Tuple[float, float]) -> Tuple[float, float]:
        """
        Bezier eğrisi kontrol noktasına varyasyon ekle
        
        Args:
            base_control: (cpx, cpy) baz kontrol noktası (0-1 aralığında)
        
        Returns:
            Varyasyon eklenmiş kontrol noktası
        """
        cpx, cpy = base_control
        variation = self.params.curve_variation
        
        # Kontrol noktasına rastgele sapma ekle
        new_cpx = cpx + random.uniform(-variation, variation)
        new_cpy = cpy + random.uniform(-variation, variation)
        
        # Sınırları koru (0.1-0.9 arası)
        new_cpx = max(0.1, min(0.9, new_cpx))
        new_cpy = max(0.1, min(0.9, new_cpy))
        
        return new_cpx, new_cpy
    
    def should_overshoot(self, distance: float) -> Tuple[bool, float]:
        """
        Hedefi aşma (overshoot) davranışı kararı
        
        İnsanlar hızlı hareketlerde hedefi hafifçe aşıp geri düzeltme yapar.
        
        Args:
            distance: Hedefe olan mesafe (piksel)
        
        Returns:
            (should_overshoot, amount): Aşma yapılmalı mı, miktar (piksel)
        """
        # Sadece uzun mesafelerde ve %20 ihtimalle overshoot
        if distance < 100 or random.random() > 0.2:
            return False, 0.0
        
        # Mesafeye bağlı overshoot miktarı (2-8 piksel)
        amount = random.uniform(2.0, min(8.0, distance * 0.05))
        return True, amount
    
    def apply_overshoot(self, dx: float, dy: float) -> Tuple[float, float, float, float]:
        """
        Harekete overshoot ekle ve düzeltme vektörü döndür
        
        Args:
            dx: X delta
            dy: Y delta
        
        Returns:
            (overshoot_dx, overshoot_dy, correction_dx, correction_dy)
            İlk hareket (aşım dahil) ve düzeltme hareketi
        """
        distance = math.sqrt(dx * dx + dy * dy)
        should_os, amount = self.should_overshoot(distance)
        
        if not should_os:
            return dx, dy, 0.0, 0.0
        
        # Aşma yönü (hedefin biraz ötesi)
        angle = math.atan2(dy, dx)
        overshoot_x = dx + math.cos(angle) * amount
        overshoot_y = dy + math.sin(angle) * amount
        
        # Düzeltme (geri dönüş)
        correction_x = -math.cos(angle) * amount
        correction_y = -math.sin(angle) * amount
        
        return overshoot_x, overshoot_y, correction_x, correction_y
    
    def simulate_human_reaction(self, callback: callable, *args, **kwargs):
        """
        Callback fonksiyonunu insansı reaksiyon gecikmesi ile çalıştır
        
        Args:
            callback: Çalıştırılacak fonksiyon
            *args, **kwargs: Callback argümanları
        """
        delay = self.get_reaction_delay()
        if delay > 0:
            time.sleep(delay)
        return callback(*args, **kwargs)
    
    def get_stats(self) -> Dict[str, Any]:
        """İnsan benzeri davranış istatistikleri"""
        with self._lock:
            avg_jitter = (self.stats['total_jitter_pixels'] / self.stats['jitter_applied'] 
                       if self.stats['jitter_applied'] > 0 else 0)
            avg_delay = (self.stats['total_delay_ms'] / self.stats['delays_added']
                        if self.stats['delays_added'] > 0 else 0)
            
            return {
                'jitter_applied': self.stats['jitter_applied'],
                'avg_jitter_pixels': round(avg_jitter, 2),
                'delays_added': self.stats['delays_added'],
                'avg_delay_ms': round(avg_delay, 2),
                'params': {
                    'jitter_range': f"{self.params.jitter_min}-{self.params.jitter_max}px",
                    'delay_range': f"{self.params.delay_min_ms}-{self.params.delay_max_ms}ms"
                }
            }
    
    def reset_stats(self):
        """İstatistikleri sıfırla"""
        with self._lock:
            self.stats = {
                'jitter_applied': 0,
                'delays_added': 0,
                'total_jitter_pixels': 0.0,
                'total_delay_ms': 0.0
            }


# Test bloğu
if __name__ == "__main__":
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except:
            pass
    
    print("=" * 70)
    print("Humanizer Module Test")
    print("=" * 70)
    
    humanizer = Humanizer()
    
    # Jitter testi
    print("\n[Jitter Testi]")
    test_moves = [(100, 0), (50, 50), (10, 10), (200, 100)]
    for dx, dy in test_moves:
        adj_dx, adj_dy = humanizer.apply_humanization(dx, dy)
        print(f"  Orijinal: ({dx:4}, {dy:4}) -> Düzeltilmiş: ({adj_dx:7.2f}, {adj_dy:7.2f})")
    
    # Gecikme testi
    print("\n[Gecikme Testi]")
    for _ in range(5):
        delay = humanizer.get_click_delay()
        print(f"  Tıklama gecikmesi: {delay*1000:.1f}ms")
    
    # Reaksiyon testi
    print("\n[Reaksiyon Gecikmesi Testi]")
    for _ in range(5):
        reaction = humanizer.get_reaction_delay()
        print(f"  Reaksiyon gecikmesi: {reaction*1000:.1f}ms")
    
    # Overshoot testi
    print("\n[Overshoot Testi]")
    for dist in [50, 150, 300]:
        should_os, amount = humanizer.should_overshoot(dist)
        status = f"Evet ({amount:.1f}px)" if should_os else "Hayır"
        print(f"  Mesafe {dist}px -> Overshoot: {status}")
    
    # İstatistikler
    print("\n[İstatistikler]")
    stats = humanizer.get_stats()
    print(f"  Jitter uygulama: {stats['jitter_applied']}")
    print(f"  Ortalama jitter: {stats['avg_jitter_pixels']:.2f}px")
    print(f"  Gecikme ekleme: {stats['delays_added']}")
    print(f"  Ortalama gecikme: {stats['avg_delay_ms']:.2f}ms")
    
    print("\n" + "=" * 70)
    print("Test tamamlandı") 
