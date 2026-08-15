#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VisualAim-Research: Hotkey Manager Module
=========================================
Global tuş dinleme ve yönetim sistemi.

Özellikler:
- F2: Aimbot aç/kapa (toggle)
- F3: Recoil kontrolü aç/kapa (toggle)
- F4: Programdan güvenli çıkış
- Callback fonksiyonu desteği
- Thread-safe operasyonlar

Yazar: İhsan
Versiyon: 1.0.0
"""

import sys
import time
import threading
from typing import Callable, Dict, Optional, Any
from enum import Enum, auto

# keyboard modülü requirements.txt'de var
try:
    import keyboard
    KEYBOARD_AVAILABLE = True
except ImportError:
    KEYBOARD_AVAILABLE = False
    print("[HotkeyManager] Warning: 'keyboard' module not installed. Hotkeys disabled.")

# UI modüllerinden import
try:
    from ui.logger import get_logger
except ImportError:
    get_logger = None


class HotkeyAction(Enum):
    """Hotkey eylem tipleri"""
    TOGGLE_AIM = auto()      # F2
    TOGGLE_TRIGGER = auto()  # F3
    TOGGLE_RECOIL = auto()   # F6 (F3 ile çakışıyordu)
    EXIT = auto()            # F4
    RELOAD_CONFIG = auto()   # F5 (opsiyonel)
    CUSTOM = auto()          # Kullanıcı tanımlı


class HotkeyManager:
    """
    Global tuş dinleme yöneticisi
    
    Windows'ta global hook kullanarak uygulama odak dışındayken
    bile tuş kombinasyonlarını yakalar.
    
    Attributes:
        running: Dinleyici çalışıyor mu?
        callbacks: Eylem -> callback fonksiyonu eşleştirmesi
        states: Toggle durumları (aim_enabled, recoil_enabled)
    """
    
    # Varsayılan tuş eşleştirmeleri
    # NOT: F3 hem recoil hem trigger için kullanılıyordu; recoil F6'ya alındı.
    DEFAULT_HOTKEYS = {
        'f2': HotkeyAction.TOGGLE_AIM,
        'f3': HotkeyAction.TOGGLE_TRIGGER,
        'f4': HotkeyAction.EXIT,
        'f5': HotkeyAction.RELOAD_CONFIG,
        'f6': HotkeyAction.TOGGLE_RECOIL,
    }
    
    def __init__(self, logger=None):
        self.logger = logger or (get_logger() if get_logger else None)
        self.running = False
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # Callback kayıtları
        self._callbacks: Dict[HotkeyAction, list] = {action: [] for action in HotkeyAction}
        self._custom_hotkeys: Dict[str, list] = {}  # str -> callbacks
        
        # Toggle durumları
        self.states = {
            'aim_enabled': False,
            'trigger_enabled': False,
            'recoil_enabled': False,
        }
        
        # Tuş durum takibi (debounce için)
        self._key_states: Dict[str, bool] = {}
        self._last_press_time: Dict[str, float] = {}
        self._debounce_interval = 0.3  # saniye
        
        if self.logger:
            self.logger.info("[HotkeyManager] Initialized")
            if not KEYBOARD_AVAILABLE:
                self.logger.warning("[HotkeyManager] Keyboard module not available - hotkeys disabled")
    
    def register_callback(self, action: HotkeyAction, callback: Callable[[], Any]) -> None:
        """
        Belirli bir eylem için callback kaydet
        
        Args:
            action: Hangi eylem için (TOGGLE_AIM, EXIT, vb.)
            callback: Çağrılacak fonksiyon (parametresiz)
        """
        with self._lock:
            if callback not in self._callbacks[action]:
                self._callbacks[action].append(callback)
                if self.logger:
                    self.logger.debug(f"[HotkeyManager] Callback registered for {action.name}")
    
    def register_custom_hotkey(self, key: str, callback: Callable[[], Any]) -> None:
        """
        Özel tuş kombinasyonu kaydet
        
        Args:
            key: Tuş adı (örn: 'ctrl+shift+x', 'f6')
            callback: Çağrılacak fonksiyon
        """
        with self._lock:
            key = key.lower()
            if key not in self._custom_hotkeys:
                self._custom_hotkeys[key] = []
            self._custom_hotkeys[key].append(callback)
            
            # Eğer çalışıyorsa hemen kaydet
            if self.running and KEYBOARD_AVAILABLE:
                keyboard.add_hotkey(key, lambda: self._handle_custom(key))
            
            if self.logger:
                self.logger.info(f"[HotkeyManager] Custom hotkey registered: {key}")
    
    def unregister_callback(self, action: HotkeyAction, callback: Callable[[], Any]) -> None:
        """Callback'i kaldır"""
        with self._lock:
            if callback in self._callbacks[action]:
                self._callbacks[action].remove(callback)
    
    def _handle_action(self, action: HotkeyAction) -> None:
        """
        Eylem gerçekleştiğinde ilgili callback'leri çalıştır
        
        Args:
            action: Gerçekleşen eylem
        """
        with self._lock:
            # Toggle durumlarını güncelle
            if action == HotkeyAction.TOGGLE_AIM:
                self.states['aim_enabled'] = not self.states['aim_enabled']
                if self.logger:
                    self.logger.success(f"[HotkeyManager] Aimbot: {'AKTİF' if self.states['aim_enabled'] else 'PASİF'}")
            
            elif action == HotkeyAction.TOGGLE_TRIGGER:
                self.states['trigger_enabled'] = not self.states['trigger_enabled']
                if self.logger:
                    self.logger.success(f"[HotkeyManager] Triggerbot: {'AKTİF' if self.states['trigger_enabled'] else 'PASİF'}")

            elif action == HotkeyAction.TOGGLE_RECOIL:
                self.states['recoil_enabled'] = not self.states['recoil_enabled']
                if self.logger:
                    self.logger.success(f"[HotkeyManager] Recoil: {'AKTİF' if self.states['recoil_enabled'] else 'PASİF'}")
            
            elif action == HotkeyAction.EXIT:
                if self.logger:
                    self.logger.warning("[HotkeyManager] Exit hotkey pressed - initiating shutdown")
            
            # Callback'leri çalıştır
            for callback in self._callbacks[action]:
                try:
                    callback()
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"[HotkeyManager] Callback error: {e}")
    
    def _handle_custom(self, key: str) -> None:
        """Özel tuş için callback'leri çalıştır"""
        with self._lock:
            if key in self._custom_hotkeys:
                for callback in self._custom_hotkeys[key]:
                    try:
                        callback()
                    except Exception as e:
                        if self.logger:
                            self.logger.error(f"[HotkeyManager] Custom callback error: {e}")
    
    def _check_debounce(self, key: str) -> bool:
        """
        Tuş bounce kontrolü (çift tetiklemeyi önle)
        
        Args:
            key: Kontrol edilecek tuş
        
        Returns:
            True: Tuş işlenebilir, False: Debounce aktif
        """
        current_time = time.time()
        
        if key in self._last_press_time:
            elapsed = current_time - self._last_press_time[key]
            if elapsed < self._debounce_interval:
                return False
        
        self._last_press_time[key] = current_time
        return True
    
    def _hotkey_loop(self) -> None:
        """Tuş dinleme döngüsü (ayrı thread'de çalışır)"""
        if not KEYBOARD_AVAILABLE:
            return
        
        # Varsayılan hotkey'leri kaydet
        for key, action in self.DEFAULT_HOTKEYS.items():
            try:
                # k=key varsayılan argümanı ŞART: 'key' döngü değişkeni olduğu
                # için closure son değeri ('f6') yakalıyor ve tüm tuşlar aynı
                # debounce kaydını paylaşıyordu (F2'den sonra F3 yutuluyordu).
                keyboard.add_hotkey(
                    key,
                    lambda a=action, k=key: self._handle_action(a) if self._check_debounce(k) else None
                )
            except Exception as e:
                if self.logger:
                    self.logger.error(f"[HotkeyManager] Failed to register {key}: {e}")
        
        # Özel hotkey'leri kaydet
        for key in self._custom_hotkeys.keys():
            try:
                keyboard.add_hotkey(key, lambda k=key: self._handle_custom(k))
            except Exception as e:
                if self.logger:
                    self.logger.error(f"[HotkeyManager] Failed to register custom {key}: {e}")
        
        if self.logger:
            self.logger.info("[HotkeyManager] Hotkeys registered - F2: Aim, F3: Trigger, F6: Recoil, F4: Exit")
        
        # Döngü - keyboard.wait() bloke edici olduğu için event kullanıyoruz
        while not self._stop_event.is_set():
            time.sleep(0.1)
    
    def start(self) -> bool:
        """
        Tuş dinleyiciyi başlat
        
        Returns:
            True: Başarılı, False: Başarısız
        """
        if not KEYBOARD_AVAILABLE:
            if self.logger:
                self.logger.error("[HotkeyManager] Cannot start - keyboard module not available")
            return False
        
        with self._lock:
            if self.running:
                return True
            
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._hotkey_loop, daemon=True)
            self._thread.start()
            self.running = True
            
            if self.logger:
                self.logger.success("[HotkeyManager] Started")
            
            return True
    
    def stop(self) -> None:
        """Tuş dinleyiciyi durdur"""
        with self._lock:
            if not self.running:
                return
            
            self._stop_event.set()
            
            if self._thread:
                self._thread.join(timeout=1.0)
                self._thread = None
            
            # Keyboard hook'larını temizle
            if KEYBOARD_AVAILABLE:
                try:
                    keyboard.unhook_all()
                except:
                    pass
            
            self.running = False
            
            if self.logger:
                self.logger.info("[HotkeyManager] Stopped")
    
    def get_state(self, key: str) -> bool:
        """
        Toggle durumunu al
        
        Args:
            key: 'aim_enabled' veya 'recoil_enabled'
        
        Returns:
            Durum değeri
        """
        with self._lock:
            return self.states.get(key, False)
    
    def set_state(self, key: str, value: bool) -> None:
        """
        Toggle durumunu manuel ayarla
        
        Args:
            key: 'aim_enabled' veya 'recoil_enabled'
            value: Yeni değer
        """
        with self._lock:
            self.states[key] = value
    
    def is_running(self) -> bool:
        """Dinleyici çalışıyor mu?"""
        return self.running
    
    def get_registered_hotkeys(self) -> Dict[str, str]:
        """Kayıtlı tuşları listele"""
        result = {}
        for key, action in self.DEFAULT_HOTKEYS.items():
            result[key.upper()] = action.name
        for key in self._custom_hotkeys.keys():
            result[key.upper()] = "CUSTOM"
        return result


# Test bloğu
if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except:
            pass
    
    print("=" * 70)
    print("Hotkey Manager Test")
    print("=" * 70)
    
    if not KEYBOARD_AVAILABLE:
        print("\n[UYARI] 'keyboard' modülü kurulu değil!")
        print("Kurulum: pip install keyboard")
        print("Test sonlandırılıyor.")
        sys.exit(1)
    
    manager = HotkeyManager()
    
    # Test callback'leri
    def on_aim_toggle():
        print(f"[Callback] Aim toggled! Current state: {manager.get_state('aim_enabled')}")
    
    def on_recoil_toggle():
        print(f"[Callback] Recoil toggled! Current state: {manager.get_state('recoil_enabled')}")
    
    def on_exit():
        print("[Callback] Exit requested!")
        manager.stop()
    
    # Callback'leri kaydet
    manager.register_callback(HotkeyAction.TOGGLE_AIM, on_aim_toggle)
    manager.register_callback(HotkeyAction.TOGGLE_RECOIL, on_recoil_toggle)
    manager.register_callback(HotkeyAction.EXIT, on_exit)
    
    # Özel hotkey örneği
    def custom_action():
        print("[Callback] Custom F6 action!")
    
    manager.register_custom_hotkey('f6', custom_action)
    
    # Başlat
    print("\n[Hotkey'ler]")
    for key, action in manager.get_registered_hotkeys().items():
        print(f"  {key}: {action}")
    
    print("\n[Test] Çıkmak için F4 tuşuna basın")
    print("[Test] Aim toggle: F2, Recoil toggle: F3")
    print("[Test] Custom action: F6\n")
    
    manager.start()
    
    # Ana döngü
    try:
        while manager.is_running():
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n[Interrupt] Ctrl+C")
    finally:
        manager.stop()
    
    print("\n" + "=" * 70)
    print("Test tamamlandı") 
