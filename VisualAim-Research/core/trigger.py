"""
core/trigger.py
VisualAim-Research Triggerbot Module
Developer: İhsan
Version: 3.0 Professional

Description:
    Automatic firing system when crosshair is on target.
    Supports multiple activation modes and weapon-specific timing.

Features:
    - Distance-based trigger threshold
    - Human-like randomized delays
    - Weapon-specific fire rate optimization
    - Burst mode detection
    - Thread-safe operations

Mathematical Model:
    Fire Decision: d ≤ threshold AND t_delay ≥ random(min, max)
    where d = √((tx-cx)² + (ty-cy)²)
"""

import time
import random
import threading
from typing import Optional, Tuple
from dataclasses import dataclass
from enum import Enum

try:
    # Paket olarak import edildiginde: from core.trigger import TriggerBot
    from .input_controller import InputController
except ImportError:
    # Dogrudan calistirildiginda: python core/trigger.py
    from input_controller import InputController


class TriggerMode(Enum):
    """Trigger activation modes"""
    ALWAYS_ON = "always_on"      # Always active when target found
    HOLD_KEY = "hold_key"        # Active while key held (e.g., mouse4)
    TOGGLE = "toggle"            # Toggle on/off with key press
    AIM_KEY = "aim_key"          # Only when aiming (right mouse)


class TriggerWeapon(Enum):
    """Weapon-specific fire rates for optimal timing"""
    VANDAL = "vandal"        # 9.75 RPS, 102ms between shots
    PHANTOM = "phantom"      # 11 RPS, 91ms between shots
    SPECTRE = "spectre"      # 13.33 RPS, 75ms between shots
    SHERIFF = "sheriff"      # 4 RPS, 250ms between shots
    GUARDIAN = "guardian"    # 5.25 RPS, 190ms between shots
    CLASSIC = "classic"      # 6.75 RPS, 148ms between shots
    GHOST = "ghost"          # 6.75 RPS, 148ms between shots
    FRENZY = "frenzy"        # 10 RPS, 100ms between shots
    MARSHAL = "marshal"      # 1.5 RPS, 667ms between shots
    OPERATOR = "operator"    # 0.6 RPS, 1667ms between shots
    CUSTOM = "custom"        # User defined


@dataclass
class TriggerConfig:
    """Trigger configuration"""
    threshold: int = 8              # Pixel distance threshold
    delay_min: int = 10             # Minimum delay (ms)
    delay_max: int = 50             # Maximum delay (ms)
    burst_delay: int = 100          # Delay between bursts (ms)
    first_shot_delay: int = 20      # Extra delay for first shot
    mode: TriggerMode = TriggerMode.HOLD_KEY
    weapon: TriggerWeapon = TriggerWeapon.VANDAL


class TriggerBot:
    """
    Professional triggerbot with human-like characteristics
    
    Features:
        - Crosshair proximity detection
        - Randomized timing (anti-detection)
        - Weapon-specific optimization
        - Cooldown management
        - Thread-safe firing
    """
    
    def __init__(self, input_controller: InputController, 
                 config: Optional[TriggerConfig] = None):
        self.input = input_controller
        self.config = config if config else TriggerConfig()
        
        # State
        self.enabled = False
        self.last_shot_time = 0.0
        self.shot_count = 0
        self.is_on_target = False
        
        # Weapon timing
        self.weapon_delays = {
            TriggerWeapon.VANDAL: 0.102,
            TriggerWeapon.PHANTOM: 0.091,
            TriggerWeapon.SPECTRE: 0.075,
            TriggerWeapon.SHERIFF: 0.250,
            TriggerWeapon.GUARDIAN: 0.190,
            TriggerWeapon.CLASSIC: 0.148,
            TriggerWeapon.GHOST: 0.148,
            TriggerWeapon.FRENZY: 0.100,
            TriggerWeapon.MARSHAL: 0.667,
            TriggerWeapon.OPERATOR: 1.667,
        }
        
        # Thread safety
        self._lock = threading.RLock()
        self._fire_thread: Optional[threading.Thread] = None
        
        # Statistics
        self.total_shots = 0
        self.total_triggers = 0
        
        print(f"[TriggerBot] Initialized ({self.config.weapon.value})")
        print(f"[TriggerBot] Mode: {self.config.mode.value}")
        print(f"[TriggerBot] Threshold: {self.config.threshold}px")
    
    # Sanal tuş kodları (mod kontrolü için)
    VK_RBUTTON = 0x02        # sağ tık - aim_key modu
    VK_XBUTTON1 = 0x05       # mouse4
    VK_XBUTTON2 = 0x06       # mouse5 (varsayılan activation_key)

    def is_key_held(self, vk_code: int) -> bool:
        """
        Sanal tuş şu anda basılı mı (yüksek bit)

        Args:
            vk_code: Windows sanal tuş kodu (ör. 0x06 = mouse5)
        """
        try:
            import ctypes
            return bool(ctypes.windll.user32.GetAsyncKeyState(vk_code) & 0x8000)
        except Exception:
            return False

    def mode_allows_fire(self, activation_key: Optional[int] = None,
                         toggle_state: bool = True) -> bool:
        """
        Tetik modu şu an ateşlemeye izin veriyor mu?

        Mod hiç kontrol edilmiyordu: hold_key/aim_key seçili olsa bile tuşa
        basılmadan ateş ediliyordu.

        Args:
            activation_key: hold_key modunda beklenen tuş (None ise mouse5)
            toggle_state: toggle modunda dışarıdan gelen aç/kapa durumu

        Returns:
            True: ateşlemeye izin var
        """
        mode = self.config.mode

        if mode == TriggerMode.ALWAYS_ON:
            return True
        if mode == TriggerMode.TOGGLE:
            return bool(toggle_state)
        if mode == TriggerMode.AIM_KEY:
            return self.is_key_held(self.VK_RBUTTON)
        if mode == TriggerMode.HOLD_KEY:
            return self.is_key_held(activation_key if activation_key is not None
                                    else self.VK_XBUTTON2)
        return False

    def check_target(self, target_x: int, target_y: int,
                    crosshair_x: int, crosshair_y: int) -> bool:
        """
        Check if crosshair is on target
        
        Args:
            target_x, target_y: Target position (usually head)
            crosshair_x, crosshair_y: Current crosshair position
            
        Returns:
            True if within threshold
        """
        distance = ((target_x - crosshair_x) ** 2 + 
                   (target_y - crosshair_y) ** 2) ** 0.5
        
        self.is_on_target = distance <= self.config.threshold
        
        if self.is_on_target:
            self.total_triggers += 1
            
        return self.is_on_target
    
    def should_fire(self) -> bool:
        """
        Determine if should fire based on timing
        
        Returns:
            True if ready to fire
        """
        if not self.is_on_target:
            return False
        
        current_time = time.time()
        
        # Weapon-specific cooldown
        min_delay = self.weapon_delays.get(self.config.weapon, 0.100)
        
        # Check cooldown
        if current_time - self.last_shot_time < min_delay:
            return False
        
        return True
    
    def fire(self) -> bool:
        """
        Execute fire with human-like delays
        
        Returns:
            True if fired successfully
        """
        with self._lock:
            if not self.should_fire():
                return False
            
            # Calculate randomized delay
            if self.shot_count == 0:
                # First shot - longer delay
                base_delay = self.config.first_shot_delay
            else:
                base_delay = random.randint(self.config.delay_min, 
                                          self.config.delay_max)
            
            # Add small randomization (±10%)
            delay_ms = base_delay * random.uniform(0.9, 1.1)
            
            # Execute delay
            time.sleep(delay_ms / 1000.0)
            
            # Fire
            self.input.click_left()
            
            # Update state
            self.last_shot_time = time.time()
            self.shot_count += 1
            self.total_shots += 1
            
            return True
    
    def fire_async(self, target_x: int, target_y: int,
                   crosshair_x: int, crosshair_y: int) -> None:
        """
        Fire asynchronously (non-blocking)
        
        Args:
            target_x, target_y: Target position
            crosshair_x, crosshair_y: Crosshair position
        """
        if not self.check_target(target_x, target_y, crosshair_x, crosshair_y):
            return
        
        # Don't start new thread if already firing
        if self._fire_thread and self._fire_thread.is_alive():
            return
        
        self._fire_thread = threading.Thread(target=self.fire, daemon=True)
        self._fire_thread.start()
    
    def reset(self) -> None:
        """Reset shot counter (call when target lost)"""
        self.shot_count = 0
        self.is_on_target = False
    
    def set_weapon(self, weapon: TriggerWeapon) -> None:
        """Change weapon"""
        self.config.weapon = weapon
        print(f"[TriggerBot] Weapon changed to {weapon.value}")
    
    def set_threshold(self, pixels: int) -> None:
        """Set trigger threshold"""
        self.config.threshold = max(2, min(50, pixels))
        print(f"[TriggerBot] Threshold set to {self.config.threshold}px")
    
    def set_mode(self, mode: TriggerMode) -> None:
        """Set trigger mode"""
        self.config.mode = mode
        print(f"[TriggerBot] Mode set to {mode.value}")
    
    def enable(self) -> None:
        """Enable triggerbot"""
        self.enabled = True
        print("[TriggerBot] Enabled")
    
    def disable(self) -> None:
        """Disable triggerbot"""
        self.enabled = False
        self.reset()
        print("[TriggerBot] Disabled")
    
    def toggle(self) -> bool:
        """Toggle enabled state"""
        if self.enabled:
            self.disable()
        else:
            self.enable()
        return self.enabled
    
    def get_stats(self) -> dict:
        """Get trigger statistics"""
        return {
            "enabled": self.enabled,
            "total_shots": self.total_shots,
            "total_triggers": self.total_triggers,
            "efficiency": (self.total_shots / self.total_triggers * 100) 
                         if self.total_triggers > 0 else 0,
            "weapon": self.config.weapon.value,
            "threshold": self.config.threshold,
            "mode": self.config.mode.value
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

    print("=" * 70)
    print("TriggerBot Test")
    print("Developer: İhsan")
    print("=" * 70)
    
    input_ctrl = InputController()
    trigger = TriggerBot(input_ctrl, TriggerConfig(
        threshold=10,
        delay_min=10,
        delay_max=30,
        weapon=TriggerWeapon.VANDAL
    ))
    
    print("\n[Test] Fareyi hareket ettirin, 'T' tuşu ile toggle yapın")
    print("[Test] Çıkmak için 'Q'\n")
    
    import keyboard
    
    try:
        while True:
            # Toggle with T
            if keyboard.is_pressed('t'):
                state = trigger.toggle()
                print(f"TriggerBot: {'ON' if state else 'OFF'}")
                time.sleep(0.3)
            
            # Exit with Q
            if keyboard.is_pressed('q'):
                break
            
            # Simulate target at center
            screen_center = input_ctrl.screen_width // 2, input_ctrl.screen_height // 2
            
            # Check if mouse near center (simulating aim)
            # NOT: 'import ctypes' tek basina ctypes.wintypes'i tanimlamaz
            # (AttributeError). InputController'in hazir metodunu kullan.
            cursor_x, cursor_y = input_ctrl.get_cursor_pos()

            if trigger.check_target(screen_center[0], screen_center[1],
                                   cursor_x, cursor_y):
                if trigger.enabled:
                    if trigger.fire():
                        print(f"\rShot fired! Total: {trigger.total_shots}", end="")
            
            time.sleep(0.016)  # ~60 FPS
            
    except KeyboardInterrupt:
        pass
    
    print(f"\n\nStats: {trigger.get_stats()}")
    print("=" * 70) 
