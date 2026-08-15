"""
core/recoil.py
VisualAim-Research Recoil Control System (RCS)
Developer: İhsan
Version: 3.0 Professional

Description:
    Mathematical recoil compensation for Valorant weapons.
    Uses predefined patterns and adaptive correction based on shot count.

Supported Weapons:
    - Vandal (9.75 RPS, aggressive vertical climb)
    - Phantom (11 RPS, linear pattern)
    - Spectre (13.33 RPS, SMG spray)
    - Sheriff (4 RPS, high per-shot kick)
    - Guardian (5.25 RPS, semi-auto)

Mathematical Model:
    Compensation(t) = Pattern(t) * CompensationFactor + RandomJitter
"""

import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum
import random
import time


class WeaponType(Enum):
    """Valorant weapon classifications"""
    VANDAL = "vandal"
    PHANTOM = "phantom"
    SPECTRE = "spectre"
    SHERIFF = "sheriff"
    GUARDIAN = "guardian"
    BULLDOG = "bulldog"
    STINGER = "stinger"
    ODIN = "odin"
    ARES = "ares"
    CLASSIC = "classic"
    GHOST = "ghost"
    FRENZY = "frenzy"
    MARSHAL = "marshal"
    OPERATOR = "operator"
    OUTLAW = "outlaw"
    CUSTOM = "custom"


@dataclass
class WeaponStats:
    """Weapon recoil characteristics"""
    fire_rate: float  # Rounds per second
    first_shot_accuracy: float  # Degrees
    vertical_recoil: float  # Base vertical climb per shot
    horizontal_recoil: float  # Base horizontal spread
    recovery_time: float  # Seconds to reset
    pattern_length: int  # Number of bullets in controllable pattern
    name: str


class RecoilPattern:
    """
    Mathematical recoil pattern for a specific weapon
    
    Each pattern is a list of (x, y) offsets representing
    where the crosshair moves per bullet fired.
    """
    
    def __init__(self, weapon: WeaponType):
        self.weapon = weapon
        self.pattern: List[Tuple[float, float]] = []
        self._generate_pattern()
        
    def _generate_pattern(self):
        """Generate recoil pattern based on weapon type"""
        
        if self.weapon == WeaponType.VANDAL:
            # Vandal: Aggressive vertical, random horizontal after 3rd shot
            self.pattern = [
                (0, -15),    # Shot 1
                (0, -22),    # Shot 2
                (0, -25),    # Shot 3
                (2, -28),    # Shot 4 (right)
                (-3, -30),   # Shot 5 (left)
                (5, -32),    # Shot 6 (right)
                (-2, -34),   # Shot 7 (left)
                (3, -35),    # Shot 8
                (-4, -36),   # Shot 9
                (2, -37),    # Shot 10
            ]
            # After 10: random horizontal, continuous vertical
            
        elif self.weapon == WeaponType.PHANTOM:
            # Phantom: More linear, less horizontal
            self.pattern = [
                (0, -10),
                (0, -15),
                (0, -18),
                (1, -20),
                (-1, -22),
                (2, -23),
                (0, -24),
                (-1, -25),
                (1, -26),
                (0, -27),
            ]
            
        elif self.weapon == WeaponType.SPECTRE:
            # Spectre: Fast climb, wide horizontal
            self.pattern = [
                (0, -12),
                (1, -18),
                (-1, -22),
                (2, -26),
                (-2, -29),
                (3, -31),
                (-3, -33),
                (2, -34),
                (-2, -35),
                (1, -36),
            ]
            
        elif self.weapon == WeaponType.SHERIFF:
            # Sheriff: High per-shot kick, slow reset
            self.pattern = [
                (0, -25),
                (1, -30),
                (-1, -35),
            ]
            
        elif self.weapon == WeaponType.GUARDIAN:
            # Guardian: Low recoil, accurate
            self.pattern = [
                (0, -8),
                (0, -10),
                (0, -12),
            ]
            
        elif self.weapon == WeaponType.STINGER:
            # Stinger: Very aggressive
            self.pattern = [
                (0, -20),
                (1, -30),
                (-1, -38),
                (2, -45),
            ]
            
        else:
            # Default pattern
            self.pattern = [(0, -15) for _ in range(10)]
    
    def get_offset(self, shot_number: int) -> Tuple[float, float]:
        """
        Get recoil offset for specific shot
        
        Args:
            shot_number: 0-indexed shot number
            
        Returns:
            (x_offset, y_offset) in pixels
        """
        if shot_number < len(self.pattern):
            return self.pattern[shot_number]
        
        # Beyond pattern: extrapolate with randomness
        last_x, last_y = self.pattern[-1]

        # Desen disindaki kacinci mermi oldugumuz (1, 2, 3, ...)
        # Bu carpan olmadan y hep last_y - 2'de sabit kaliyordu, yani
        # "dikey tirmanis surer" davranisi hic gerceklesmiyordu.
        extra = shot_number - len(self.pattern) + 1

        # Vertical continues climbing (uzun spreylerde sinirsiz buyumesin)
        y = max(last_y - 2 * extra, last_y - 20)

        # Horizontal random walk: yayilim mermi sayisinin karekoku ile buyur
        # (dogrusal carpan 5-6 mermide +-10 sinirina yapisiyordu)
        x = last_x + random.uniform(-3, 3) * (extra ** 0.5)
        x = max(-10, min(10, x))  # Clamp

        return (x, y)


class RecoilController:
    """
    Professional Recoil Control System
    
    Features:
        - Weapon-specific patterns
        - Adaptive compensation based on shot count
        - Human-like randomization
        - Burst detection (reset counter on delays)
        - Moving accuracy penalty
    """
    
    def __init__(self, weapon: WeaponType = WeaponType.VANDAL):
        self.weapon = weapon
        self.pattern = RecoilPattern(weapon)
        self.stats = self._get_weapon_stats(weapon)
        
        # State
        self.shot_count = 0
        self.last_shot_time = 0
        self.is_firing = False
        
        # Compensation settings
        self.compensation_factor = 1.0  # 1.0 = full compensation
        self.randomization = 0.1  # 10% randomization
        
        # Burst detection
        self.burst_threshold = 0.3  # seconds between shots to reset
        
        print(f"[RecoilController] Initialized for {weapon.value.upper()}")
        print(f"[RecoilController] Fire rate: {self.stats.fire_rate} RPS")
        
    def _get_weapon_stats(self, weapon: WeaponType) -> WeaponStats:
        """Get weapon statistics"""
        
        stats_map = {
            WeaponType.VANDAL: WeaponStats(
                fire_rate=9.75,
                first_shot_accuracy=0.25,
                vertical_recoil=25,
                horizontal_recoil=5,
                recovery_time=0.55,
                pattern_length=10,
                name="Vandal"
            ),
            WeaponType.PHANTOM: WeaponStats(
                fire_rate=11.0,
                first_shot_accuracy=0.20,
                vertical_recoil=20,
                horizontal_recoil=3,
                recovery_time=0.50,
                pattern_length=10,
                name="Phantom"
            ),
            WeaponType.SPECTRE: WeaponStats(
                fire_rate=13.33,
                first_shot_accuracy=0.40,
                vertical_recoil=30,
                horizontal_recoil=8,
                recovery_time=0.40,
                pattern_length=10,
                name="Spectre"
            ),
            WeaponType.SHERIFF: WeaponStats(
                fire_rate=4.0,
                first_shot_accuracy=0.25,
                vertical_recoil=35,
                horizontal_recoil=4,
                recovery_time=1.0,
                pattern_length=3,
                name="Sheriff"
            ),
            WeaponType.GUARDIAN: WeaponStats(
                fire_rate=5.25,
                first_shot_accuracy=0.10,
                vertical_recoil=10,
                horizontal_recoil=2,
                recovery_time=0.30,
                pattern_length=3,
                name="Guardian"
            ),
        }
        
        return stats_map.get(weapon, stats_map[WeaponType.VANDAL])
    
    def on_shot_fired(self) -> Tuple[float, float]:
        """
        Call this when a shot is fired
        
        Returns:
            (compensation_x, compensation_y) - how much to move mouse
        """
        current_time = time.time()
        
        # Check if burst ended (reset counter)
        if current_time - self.last_shot_time > self.burst_threshold:
            self.shot_count = 0
        
        # Get pattern offset
        recoil_x, recoil_y = self.pattern.get_offset(self.shot_count)
        
        # Apply compensation (inverse of recoil)
        comp_x = -recoil_x * self.compensation_factor
        comp_y = -recoil_y * self.compensation_factor
        
        # Add human-like randomization
        if self.randomization > 0:
            comp_x += random.uniform(-self.randomization * abs(comp_x), 
                                     self.randomization * abs(comp_x))
            comp_y += random.uniform(-self.randomization * abs(comp_y), 
                                     self.randomization * abs(comp_y))
        
        # Update state
        self.shot_count += 1
        self.last_shot_time = current_time
        self.is_firing = True
        
        return (comp_x, comp_y)
    
    def on_stop_firing(self) -> None:
        """Call when firing stops"""
        self.is_firing = False
        
        # Gradual reset (optional)
        # self.shot_count = max(0, self.shot_count - 1)
    
    def get_current_recoil(self) -> Tuple[float, float]:
        """Get current recoil without firing (for prediction)"""
        if self.shot_count == 0:
            return (0, 0)
        
        recoil_x, recoil_y = self.pattern.get_offset(self.shot_count - 1)
        return (recoil_x, recoil_y)
    
    def reset(self) -> None:
        """Reset shot counter"""
        self.shot_count = 0
        self.is_firing = False
    
    def set_weapon(self, weapon: WeaponType) -> None:
        """Change weapon"""
        self.weapon = weapon
        self.pattern = RecoilPattern(weapon)
        self.stats = self._get_weapon_stats(weapon)
        self.reset()
        print(f"[RecoilController] Switched to {weapon.value.upper()}")
    
    def set_compensation(self, factor: float) -> None:
        """
        Set compensation strength
        
        Args:
            factor: 0.0 to 1.0 (1.0 = full compensation)
        """
        self.compensation_factor = max(0.0, min(1.0, factor))
    
    def get_stats(self) -> dict:
        """Get current recoil stats"""
        return {
            "weapon": self.weapon.value,
            "shot_count": self.shot_count,
            "is_firing": self.is_firing,
            "compensation": self.compensation_factor,
            "current_recoil": self.get_current_recoil()
        }


# =============================================================================
# STANDALONE TEST (Console only, no GUI)
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
    print("Recoil Control System Test")
    print("Developer: İhsan")
    print("=" * 70)
    
    # Test Vandal
    print("\n--- VANDAL TEST ---")
    rcs = RecoilController(WeaponType.VANDAL)
    
    print("Simulating 10 shots...")
    for i in range(10):
        comp_x, comp_y = rcs.on_shot_fired()
        print(f"Shot {i+1}: Recoil offset (compensate by moving {comp_x:.1f}, {comp_y:.1f})")
        time.sleep(0.102)  # Vandal fire rate
    
    print(f"\nStats: {rcs.get_stats()}")
    
    # Test reset
    print("\nWaiting for burst reset...")
    time.sleep(0.5)
    rcs.on_stop_firing()
    
    # Test Phantom
    print("\n--- PHANTOM TEST ---")
    rcs.set_weapon(WeaponType.PHANTOM)
    
    for i in range(5):
        comp_x, comp_y = rcs.on_shot_fired()
        print(f"Shot {i+1}: Compensate ({comp_x:.1f}, {comp_y:.1f})")
        time.sleep(0.091)  # Phantom fire rate
    
    print(f"\nFinal stats: {rcs.get_stats()}")
    print("=" * 70)