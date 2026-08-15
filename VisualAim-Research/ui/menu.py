#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VisualAim-Research Terminal Menu
================================
Hacker terminal estetiğiyle profesyonel, interaktif menü sistemi.
ANSI escape kodları, renkli çıktı, gerçek zamanlı durum göstergesi.

Özellikler:
- Neon yeşil hacker teması
- Çok seviyeli menü yapısı (Ana menü + alt menüler)
- Gerçek zamanlı durum çubuğu (FPS, Latency, Mode, Target)
- Tek tuş navigasyon (ok tuşları, sayılar, hotkeys)
- ConfigManager entegrasyonu
- Thread-safe durum güncellemeleri

Author: İhsan
Version: 2.0.0
"""

import os
import re
import sys
import time
import logging
import threading
import keyboard
from typing import Dict, List, Optional, Callable, Any, Tuple
from dataclasses import dataclass
from enum import Enum, auto

# Kendi modüllerimiz
try:
    from .logger import Colors, get_logger, ResearchLogger
    from .config_manager import ConfigManager, ConfigType
except ImportError:
    # Standalone çalışma için
    from logger import Colors, get_logger, ResearchLogger
    from config_manager import ConfigManager, ConfigType


class MenuState(Enum):
    """Menü durumları"""
    MAIN = auto()
    PERFORMANCE = auto()
    AIMBOT = auto()
    TRIGGER = auto()
    RECOIL = auto()
    DEBUG = auto()
    PROFILES = auto()
    LOGS = auto()


@dataclass
class MenuItem:
    """Menü öğesi"""
    id: str
    label: str
    shortcut: str
    action: Optional[Callable] = None
    submenu: Optional['Menu'] = None
    disabled: bool = False
    description: str = ""


class TerminalMenu:
    """
    Ana terminal menü sınıfı
    
    Hacker temalı, interaktif menü sistemi.
    """
    
    # Kenarlık karakterleri
    BOX_CHARS = {
        'h': '═', 'v': '║',
        'tl': '╔', 'tr': '╗',
        'bl': '╚', 'br': '╝',
        'ml': '╠', 'mr': '╣',
        'mm': '╬',
    }
    
    # ANSI escape dizileri (genişlik hesabında sayılmamalı)
    _ANSI_PATTERN = re.compile(r'\033\[[0-9;]*m')

    # Alternatif ASCII karakterler (destek yoksa)
    ASCII_CHARS = {
        'h': '=', 'v': '|',
        'tl': '+', 'tr': '+',
        'bl': '+', 'br': '+',
        'ml': '+', 'mr': '+',
        'mm': '+',
    }
    
    def __init__(
        self,
        config: Optional[ConfigManager] = None,
        logger: Optional[ResearchLogger] = None,
        global_hotkeys: bool = True
    ):
        """
        Args:
            config: Paylaşılan ConfigManager (None ise yenisi açılır)
            logger: Paylaşılan logger
            global_hotkeys: F1-F4 için global klavye kancası kur.
                main.py ile birlikte çalışırken False verilmeli; aynı tuşlar
                modules/hotkey_manager.py tarafından zaten dinleniyor ve
                iki kanca birden tetiklenip durumu ters çeviriyor.
        """
        self.config = config or ConfigManager()
        self.logger = logger or get_logger()
        
        # Durum
        self.running = True
        self.current_state = MenuState.MAIN
        self.selected_index = 0
        self.previous_state: Optional[MenuState] = None
        self.state_history: List[MenuState] = []
        
        # Ekran boyutları
        self.width = 70
        self.height = 25
        
        # Durum bilgileri (gerçek zamanlı güncellenir)
        self.status = {
            'bot_running': False,
            'fps': 0.0,
            'latency_ms': 0.0,
            'mode': 'SMOOTH',
            'target': '--',
            'profile': 'BALANCED',
            'aim_enabled': False,
            'trigger_enabled': False,
        }
        
        # Menü yapısı
        self._needs_redraw = True
        self._build_menus()
        
        # Threading
        self._update_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # Terminal ayarları
        self._use_unicode = self._check_unicode_support()
        self._chars = self.BOX_CHARS if self._use_unicode else self.ASCII_CHARS
        
        # Klavye hook'ları (main.py ile birlikte çalışırken kapalı)
        self._global_hotkeys = global_hotkeys
        if global_hotkeys:
            self._setup_hotkeys()
    
    def _check_unicode_support(self) -> bool:
        """Unicode desteğini kontrol et"""
        try:
            # Windows'ta chcp 65001 (UTF-8) kontrolü
            import ctypes
            return True
        except:
            return False
    
    def _setup_hotkeys(self) -> None:
        """Global hotkey'leri ayarla"""
        # F tuşları menü navigasyonu için
        keyboard.on_press_key('f1', lambda e: self._hotkey_handler('f1'))
        keyboard.on_press_key('f2', lambda e: self._hotkey_handler('f2'))
        keyboard.on_press_key('f3', lambda e: self._hotkey_handler('f3'))
        keyboard.on_press_key('f4', lambda e: self._hotkey_handler('f4'))
        keyboard.on_press_key('esc', lambda e: self._hotkey_handler('esc'))
    
    def _hotkey_handler(self, key: str) -> None:
        """Hotkey olay işleyici"""
        if key == 'f1':
            self._show_help()
        elif key == 'f2':
            self.status['aim_enabled'] = not self.status['aim_enabled']
            self.config.set('aim.enabled', self.status['aim_enabled'])
            self.logger.success(f"Aimbot: {'AKTİF' if self.status['aim_enabled'] else 'PASİF'}")
        elif key == 'f3':
            self.status['trigger_enabled'] = not self.status['trigger_enabled']
            self.config.set('trigger.enabled', self.status['trigger_enabled'])
            self.logger.success(f"Triggerbot: {'AKTİF' if self.status['trigger_enabled'] else 'PASİF'}")
        elif key == 'f4':
            self.running = False
        elif key == 'esc':
            self._go_back()

        # Hotkey'ler ayrı bir thread'den gelir; ekranın tazelenmesi gerekir
        self._needs_redraw = True
    
    def _build_menus(self) -> None:
        """
        Menü yapısını oluştur

        Etiketlere config değerleri gömülü olduğu için her çizimden önce
        yeniden üretilir; aksi halde ayar değiştirildiğinde menüde eski
        değer görünmeye devam eder.
        """
        # Alt menüler ÖNCE üretilir, ana menü bu nesnelere referans verir.
        # (Daha önce alt menüler ikişer kez ayrı ayrı üretiliyordu; bu yüzden
        #  _get_state_from_menu() kimlik karşılaştırması hiçbir zaman tutmuyor
        #  ve alt menülere girilemiyordu - her seçim ana menüde kalıyordu.)
        self.menus = {
            MenuState.PERFORMANCE: self._build_performance_menu(),
            MenuState.AIMBOT: self._build_aimbot_menu(),
            MenuState.TRIGGER: self._build_trigger_menu(),
            MenuState.RECOIL: self._build_recoil_menu(),
            MenuState.DEBUG: self._build_debug_menu(),
        }

        # Ana Menü
        self.main_menu = [
            MenuItem('1', 'START BOT', '1', action=self._start_bot),
            MenuItem('2', 'STOP BOT', '2', action=self._stop_bot),
            MenuItem('3', 'PERFORMANCE PROFILES', '3',
                    submenu=self.menus[MenuState.PERFORMANCE]),
            MenuItem('4', 'AIMBOT SETTINGS', '4',
                    submenu=self.menus[MenuState.AIMBOT]),
            MenuItem('5', 'TRIGGERBOT SETTINGS', '5',
                    submenu=self.menus[MenuState.TRIGGER]),
            MenuItem('6', 'RECOIL CONTROL', '6',
                    submenu=self.menus[MenuState.RECOIL]),
            MenuItem('7', 'DISPLAY / DEBUG', '7',
                    submenu=self.menus[MenuState.DEBUG]),
            MenuItem('8', 'LOAD PROFILE', '8', action=self._load_profile),
            MenuItem('9', 'SAVE PROFILE', '9', action=self._save_profile),
            # Kısayol tek karakter okunuyor: '10' hiçbir zaman eşleşmiyordu
            MenuItem('10', 'VIEW LOGS', 'l', action=self._view_logs),
            MenuItem('0', 'EXIT', '0', action=self._exit),
        ]

        self.menus[MenuState.MAIN] = self.main_menu

    def _build_performance_menu(self) -> List[MenuItem]:
        """Performans profilleri menüsü"""
        return [
            MenuItem('1', 'STEALTH  - 30 FPS | Low CPU | Human-like', '1',
                    action=lambda: self._set_profile('stealth')),
            MenuItem('2', 'BALANCED - 60 FPS | Medium CPU | Recommended', '2',
                    action=lambda: self._set_profile('balanced')),
            MenuItem('3', 'RAPID    - 90 FPS | High CPU | Fast reaction', '3',
                    action=lambda: self._set_profile('rapid')),
            MenuItem('4', 'ULTRA    - 144 FPS | Max CPU | Instant snap', '4',
                    action=lambda: self._set_profile('ultra')),
            MenuItem('5', 'CUSTOM   - Manual settings', '5',
                    action=lambda: self._set_profile('custom')),
            MenuItem('0', 'BACK', '0', action=self._go_back),
        ]
    
    def _build_aimbot_menu(self) -> List[MenuItem]:
        """Aimbot ayarları menüsü"""
        return [
            MenuItem('1', f'Aim Mode: [{self._get_aim_mode()}]', '1',
                    action=self._cycle_aim_mode),
            MenuItem('2', f'Speed: {self.config.get("aim.speed"):.2f}', '2',
                    action=self._adjust_speed),
            MenuItem('3', f'Smoothing: {self.config.get("aim.smoothing"):.2f}', '3',
                    action=self._adjust_smoothing),
            MenuItem('4', f'FOV Radius: {self.config.get("aim.fov_radius")}', '4',
                    action=self._adjust_fov),
            MenuItem('5', f'Head Offset: {self.config.get("aim.head_offset"):.2f}', '5',
                    action=self._adjust_head_offset),
            MenuItem('6', 'Reset to Default', '6',
                    action=self._reset_aim_defaults),
            MenuItem('0', 'BACK', '0', action=self._go_back),
        ]
    
    def _build_trigger_menu(self) -> List[MenuItem]:
        """Triggerbot ayarları menüsü"""
        return [
            MenuItem('1', f'Enable/Disable: [{"ENABLED" if self.config.get("trigger.enabled") else "DISABLED"}]', '1',
                    action=self._toggle_trigger),
            MenuItem('2', f'Threshold: {self.config.get("trigger.threshold")}', '2',
                    action=self._adjust_trigger_threshold),
            MenuItem('3', f'Delay Min: {self.config.get("trigger.delay_min")}ms', '3',
                    action=self._adjust_trigger_delay_min),
            MenuItem('4', f'Delay Max: {self.config.get("trigger.delay_max")}ms', '4',
                    action=self._adjust_trigger_delay_max),
            MenuItem('0', 'BACK', '0', action=self._go_back),
        ]
    
    def _build_recoil_menu(self) -> List[MenuItem]:
        """Recoil kontrol menüsü"""
        return [
            MenuItem('1', f'Recoil Y: {self.config.get("recoil.compensation_y"):.1f}', '1',
                    action=self._adjust_recoil_y),
            MenuItem('2', f'Recoil X: {self.config.get("recoil.compensation_x"):.1f}', '2',
                    action=self._adjust_recoil_x),
            MenuItem('3', f'Weapon: {self.config.get("recoil.weapon").upper()}', '3',
                    action=self._cycle_weapon),
            MenuItem('0', 'BACK', '0', action=self._go_back),
        ]
    
    def _build_debug_menu(self) -> List[MenuItem]:
        """Debug ayarları menüsü"""
        return [
            MenuItem('1', f'Debug Mode: [{"ON" if self.config.get("debug.enabled") else "OFF"}]', '1',
                    action=self._toggle_debug),
            MenuItem('2', f'Show Overlay: [{"ON" if self.config.get("debug.show_overlay") else "OFF"}]', '2',
                    action=self._toggle_overlay),
            MenuItem('3', f'Log Level: {self.config.get("debug.log_level")}', '3',
                    action=self._cycle_log_level),
            MenuItem('4', 'Save Screenshot on Error', '4',
                    action=self._toggle_screenshot),
            MenuItem('0', 'BACK', '0', action=self._go_back),
        ]
    
    # Çizim metodları
    def _clear(self) -> None:
        """Ekranı temizle"""
        os.system('cls' if os.name == 'nt' else 'clear')
    
    def _print_banner(self) -> None:
        """ASCII banner yazdır"""
        banner = f"""
{Colors.NEON_GREEN}{Colors.BOLD}
╔══════════════════════════════════════════════════════════════╗
║   ██╗   ██╗██╗███████╗██╗   ██╗ █████╗ ██╗     ██████╗       ║
║   ██║   ██║██║██╔════╝██║   ██║██╔══██╗██║     ██╔══██╗      ║
║   ██║   ██║██║███████╗██║   ██║███████║██║     ██████╔╝      ║
║   ╚██╗ ██╔╝██║╚════██║██║   ██║██╔══██║██║     ██╔══██╗      ║
║    ╚████╔╝ ██║███████║╚██████╔╝██║  ██║███████╗██║  ██║      ║
║     ╚═══╝  ╚═╝╚══════╝ ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝      ║
║                                                              ║
║              VISUALAIM-RESEARCH v2.0.0                       ║
║              ═══ Powered by İhsan ═══                        ║
╚══════════════════════════════════════════════════════════════╝{Colors.RESET}
        """
        print(banner)
    
    @staticmethod
    def _visible_len(text: str) -> int:
        """ANSI renk kodlarını saymadan görünen karakter sayısını döndür"""
        return len(TerminalMenu._ANSI_PATTERN.sub('', text))

    def _draw_box(self, title: str, content: List[str], width: int = 68) -> None:
        """Kutu çerçeve çiz"""
        c = self._chars

        # Üst kenar
        print(f"{Colors.NEON_CYAN}{c['tl']}{c['h'] * (width-2)}{c['tr']}{Colors.RESET}")

        # Başlık
        title_padding = (width - 2 - len(title)) // 2
        print(f"{Colors.NEON_CYAN}{c['v']}{Colors.RESET}"
              f"{' ' * title_padding}{Colors.BRIGHT_GREEN}{Colors.BOLD}{title}{Colors.RESET}"
              f"{' ' * (width - 2 - title_padding - len(title))}"
              f"{Colors.NEON_CYAN}{c['v']}{Colors.RESET}")

        # Ayraç
        print(f"{Colors.NEON_CYAN}{c['ml']}{c['h'] * (width-2)}{c['mr']}{Colors.RESET}")

        # İçerik
        # NOT: satırlar renk kodu içerdiği için dolgu görünen uzunluğa göre
        # hesaplanır; len() kullanılırsa sağ kenar çizgisi kayıyordu.
        for line in content:
            padding = max(0, width - 3 - self._visible_len(line))
            print(f"{Colors.NEON_CYAN}{c['v']}{Colors.RESET}"
                  f" {line}{' ' * padding}"
                  f"{Colors.NEON_CYAN}{c['v']}{Colors.RESET}")

        # Alt kenar
        print(f"{Colors.NEON_CYAN}{c['bl']}{c['h'] * (width-2)}{c['br']}{Colors.RESET}")
    
    def _draw_menu_items(self, items: List[MenuItem], selected: int) -> None:
        """Menü öğelerini çiz"""
        content = []
        for i, item in enumerate(items):
            prefix = "▶" if i == selected else " "
            color = Colors.BRIGHT_GREEN if i == selected else Colors.RESET
            dim = Colors.DIM if item.disabled else ""
            
            line = f"{prefix} [{item.shortcut}] {item.label}"
            content.append(f"{dim}{color}{line}{Colors.RESET}")
        
        self._draw_box("MENU", content)
    
    def _draw_status_bar(self) -> None:
        """Durum çubuğunu çiz"""
        c = self._chars
        
        # Durum bilgileri
        status_text = (
            f"{Colors.BRIGHT_CYAN}STATUS:{Colors.RESET} "
            f"{'RUNNING' if self.status['bot_running'] else 'READY'} | "
            f"{Colors.BRIGHT_CYAN}FPS:{Colors.RESET} {self.status['fps']:.1f} | "
            f"{Colors.BRIGHT_CYAN}LATENCY:{Colors.RESET} {self.status['latency_ms']:.1f}ms | "
            f"{Colors.BRIGHT_CYAN}MODE:{Colors.RESET} {self.status['mode']} | "
            f"{Colors.BRIGHT_CYAN}AIM:{Colors.RESET} "
            f"{'ON' if self.status['aim_enabled'] else 'OFF'} | "
            f"{Colors.BRIGHT_CYAN}TRIGGER:{Colors.RESET} "
            f"{'ON' if self.status['trigger_enabled'] else 'OFF'}"
        )
        
        # Kısalt (ekran sığsın)
        if len(status_text) > 200:  # ANSI kodları dahil
            status_text = status_text[:200]
        
        print(f"\n{Colors.NEON_CYAN}{c['h'] * self.width}{Colors.RESET}")
        print(f"{Colors.BRIGHT_GREEN}{status_text}{Colors.RESET}")
        print(f"{Colors.NEON_CYAN}{c['h'] * self.width}{Colors.RESET}")
        
        # Hotkey bilgisi
        print(f"{Colors.DIM}F1: Help | F2: Toggle Aim | F3: Toggle Trigger | F4: Exit | ESC: Back{Colors.RESET}")
    
    def _draw_current_menu(self) -> None:
        """Mevcut menüyü çiz"""
        # Etiketlerdeki config değerleri güncel olsun diye yeniden üret
        self._build_menus()

        self._clear()
        self._print_banner()

        current_menu = self.menus.get(self.current_state, self.main_menu)
        self.selected_index = max(0, min(self.selected_index, len(current_menu) - 1))
        self._draw_menu_items(current_menu, self.selected_index)
        self._draw_status_bar()
        self._needs_redraw = False
    
    # Input handling
    # NOT: _get_input() kaldırıldı - hiçbir yerden çağrılmıyordu ve Unix dalı
    # (tty/termios) Windows'ta zaten çalışmıyordu. Girdi run() içinde
    # msvcrt ile okunuyor.

    def _handle_input(self, char: str) -> None:
        """Girdi işle"""
        if not char:
            return
        
        char_lower = char.lower()
        current_menu = self.menus.get(self.current_state, self.main_menu)
        
        # Sayısal seçim
        for i, item in enumerate(current_menu):
            if item.shortcut == char or item.shortcut == char_lower:
                self._select_item(item)
                return
        
        # Yön tuşları
        if char == '\x1b[A' or char == 'w':  # Yukarı
            self.selected_index = max(0, self.selected_index - 1)
        elif char == '\x1b[B' or char == 's':  # Aşağı
            self.selected_index = min(len(current_menu) - 1, self.selected_index + 1)
        elif char == '\r' or char == ' ':  # Enter/Space
            if self.selected_index < len(current_menu):
                self._select_item(current_menu[self.selected_index])
        elif char == '\x1b':  # ESC
            self._go_back()
    
    def _select_item(self, item: MenuItem) -> None:
        """Menü öğesi seçildi"""
        if item.disabled:
            return
        
        if item.action:
            item.action()
        elif item.submenu:
            self.state_history.append(self.current_state)
            self.current_state = self._get_state_from_menu(item.submenu)
            self.selected_index = 0
    
    def _get_state_from_menu(self, menu: List[MenuItem]) -> MenuState:
        """Menü listesinden durum belirle"""
        for state, state_menu in self.menus.items():
            if state_menu is menu:
                return state
        return MenuState.MAIN
    
    def _go_back(self) -> None:
        """Bir üst menüye dön"""
        if self.state_history:
            self.current_state = self.state_history.pop()
            self.selected_index = 0
        elif self.current_state != MenuState.MAIN:
            self.current_state = MenuState.MAIN
            self.selected_index = 0
    
    # Action metodları
    def _start_bot(self) -> None:
        """Bot'u başlat"""
        self.status['bot_running'] = True
        self.logger.success("Bot başlatıldı!")
        time.sleep(0.5)
    
    def _stop_bot(self) -> None:
        """Bot'u durdur"""
        self.status['bot_running'] = False
        self.logger.warning("Bot durduruldu")
        time.sleep(0.5)
    
    def _set_profile(self, profile: str) -> None:
        """Performans profili ayarla"""
        self.status['profile'] = profile.upper()
        self.config.set('profile.active', profile)
        
        # Profil bazlı ayarlar
        profiles = {
            'stealth': {'fps': 30, 'speed': 0.2, 'smoothing': 0.4},
            'balanced': {'fps': 60, 'speed': 0.35, 'smoothing': 0.15},
            'rapid': {'fps': 90, 'speed': 0.6, 'smoothing': 0.05},
            'ultra': {'fps': 144, 'speed': 0.9, 'smoothing': 0.0},
        }
        
        if profile in profiles:
            p = profiles[profile]
            self.config.set('capture.target_fps', p['fps'])
            self.config.set('aim.speed', p['speed'])
            self.config.set('aim.smoothing', p['smoothing'])
        
        self.logger.success(f"Profil değiştirildi: {profile.upper()}")
        self._go_back()
    
    def _bump(self, key: str, step) -> None:
        """
        Sayısal ayarı bir adım artır, ŞEMA sınırında durdur

        Sınırlar menüde sabit yazılıydı (ör. fov_radius için 500) ve şema
        sınırlarıyla çelişiyordu: şema 2000'e izin verirken menü 500'de
        kısıtlıyor, "Reset to Default" ise 250'ye çekiyordu. Artık tek
        kaynak config şeması.
        """
        schema = self.config.DEFAULT_SCHEMA.get(key)
        value = self.config.get(key) + step

        if schema:
            if schema.max_value is not None and value > schema.max_value:
                value = schema.max_value
            if schema.min_value is not None and value < schema.min_value:
                value = schema.min_value
            if schema.config_type is ConfigType.INTEGER:
                value = int(round(value))

        self.config.set(key, value)
        return value

    def _next_choice(self, key: str, choices: List[str]) -> str:
        """Şema seçenekleri arasında sıradaki değeri döndür (bilinmeyen değerde ilkine döner)"""
        current = self.config.get(key)
        index = choices.index(current) if current in choices else -1
        return choices[(index + 1) % len(choices)]

    def _cycle_aim_mode(self) -> None:
        """Aim modunu değiştir"""
        # Seçenekler şemadan gelir: core/aim_controller.py AimMode ile aynı
        modes = self.config.DEFAULT_SCHEMA['aim.mode'].choices
        next_mode = self._next_choice('aim.mode', modes)
        self.config.set('aim.mode', next_mode)
        self.status['mode'] = next_mode.upper()
        self.logger.info(f"Aim mode: {next_mode.upper()}")
    
    def _adjust_speed(self) -> None:
        """Aim hızını ayarla"""
        new_val = self._bump('aim.speed', 0.05)
        self.logger.info(f"Speed: {new_val:.2f}")
    
    def _adjust_smoothing(self) -> None:
        """Smoothing ayarla"""
        new_val = self._bump('aim.smoothing', 0.05)
        self.logger.info(f"Smoothing: {new_val:.2f}")
    
    def _adjust_fov(self) -> None:
        """FOV ayarla"""
        new_val = self._bump('aim.fov_radius', 25)
        self.logger.info(f"FOV: {new_val}")
    
    def _adjust_head_offset(self) -> None:
        """Kafa ofseti ayarla"""
        new_val = self._bump('aim.head_offset', 0.02)
        self.logger.info(f"Head offset: {new_val:.2f}")
    
    def _reset_aim_defaults(self) -> None:
        """Aim ayarlarını sıfırla"""
        # Sema varsayilanlari (sabit sayilar sema ile celisiyordu)
        for key in ('aim.speed', 'aim.smoothing', 'aim.fov_radius', 'aim.head_offset'):
            schema = self.config.DEFAULT_SCHEMA.get(key)
            if schema:
                self.config.set(key, schema.default)
        self.logger.success("Aim ayarları sıfırlandı")
    
    def _toggle_trigger(self) -> None:
        """Triggerbot aç/kapat"""
        current = self.config.get('trigger.enabled')
        self.config.set('trigger.enabled', not current)
        self.logger.info(f"Triggerbot: {'ON' if not current else 'OFF'}")
    
    def _adjust_trigger_threshold(self) -> None:
        """Trigger threshold ayarla"""
        new_val = self._bump('trigger.threshold', 1)
        self.logger.info(f"Threshold: {new_val}")
    
    def _adjust_trigger_delay_min(self) -> None:
        """Trigger min gecikme ayarla"""
        new_val = self._bump('trigger.delay_min', 5)
        self.logger.info(f"Delay min: {new_val}ms")
    
    def _adjust_trigger_delay_max(self) -> None:
        """Trigger max gecikme ayarla"""
        new_val = self._bump('trigger.delay_max', 5)
        self.logger.info(f"Delay max: {new_val}ms")
    
    def _adjust_recoil_y(self) -> None:
        """Recoil Y ayarla"""
        new_val = self._bump('recoil.compensation_y', 0.1)
        self.logger.info(f"Recoil Y: {new_val:.1f}")
    
    def _adjust_recoil_x(self) -> None:
        """Recoil X ayarla"""
        new_val = self._bump('recoil.compensation_x', 0.1)
        self.logger.info(f"Recoil X: {new_val:.1f}")
    
    def _cycle_weapon(self) -> None:
        """Silah değiştir"""
        # Liste config_manager'daki recoil.weapon seçenekleriyle aynı olmalı
        # (TriggerWeapon ve WeaponType enum'larının kesişimi)
        weapons = self.config.DEFAULT_SCHEMA['recoil.weapon'].choices
        next_weapon = self._next_choice('recoil.weapon', weapons)
        self.config.set('recoil.weapon', next_weapon)
        self.logger.info(f"Weapon: {next_weapon.upper()}")
    
    def _toggle_debug(self) -> None:
        """Debug modu aç/kapat"""
        current = self.config.get('debug.enabled')
        self.config.set('debug.enabled', not current)
        self.logger.info(f"Debug: {'ON' if not current else 'OFF'}")
    
    def _toggle_overlay(self) -> None:
        """Overlay aç/kapat"""
        current = self.config.get('debug.show_overlay')
        self.config.set('debug.show_overlay', not current)
        self.logger.info(f"Overlay: {'ON' if not current else 'OFF'}")
    
    def _cycle_log_level(self) -> None:
        """Log seviyesi değiştir"""
        levels = self.config.DEFAULT_SCHEMA['debug.log_level'].choices
        next_level = self._next_choice('debug.log_level', levels)
        self.config.set('debug.log_level', next_level)
        # NOT: 'logging' modülü artık import ediliyor (önceden NameError veriyordu)
        self.logger.set_level(getattr(logging, next_level))
        self.logger.info(f"Log level: {next_level}")
    
    def _toggle_screenshot(self) -> None:
        """Screenshot ayarını değiştir"""
        current = self.config.get('debug.save_screenshots')
        self.config.set('debug.save_screenshots', not current)
        self.logger.info(f"Auto screenshot: {'ON' if not current else 'OFF'}")
    
    def _load_profile(self) -> None:
        """Profil yükle"""
        profiles = self.config.list_profiles()
        if not profiles:
            self.logger.warning("Kayıtlı profil bulunamadı")
            return
        
        print(f"\n{Colors.BRIGHT_GREEN}Mevcut profiller:{Colors.RESET}")
        for i, p in enumerate(profiles, 1):
            print(f"  {i}. {p}")
        
        try:
            choice = int(input("Profil numarası (0 iptal): "))
            if 1 <= choice <= len(profiles):
                selected = profiles[choice - 1]
                if self.config.load_profile(selected):
                    self.logger.success(f"Profil yüklendi: {selected}")
                else:
                    self.logger.error(f"Profil yüklenemedi: {selected}")
        except ValueError:
            pass
    
    def _save_profile(self) -> None:
        """Profil kaydet"""
        name = input("Profil adı: ").strip()
        if name:
            desc = input("Açıklama (opsiyonel): ").strip()
            self.config.save_profile(name, desc)
            self.logger.success(f"Profil kaydedildi: {name}")
    
    def _view_logs(self) -> None:
        """Logları görüntüle"""
        log_path = self.logger.logs_dir / "bot_logs.txt"
        if log_path.exists():
            print(f"\n{Colors.BRIGHT_GREEN}=== SON 20 LOG ==={Colors.RESET}")
            # Log dosyası UTF-8; kodlama verilmezse Türkçe karakterlerde
            # UnicodeDecodeError ile çöküyordu
            with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()[-20:]
                for line in lines:
                    print(line.rstrip())
            input("\nDevam etmek için Enter...")
        else:
            self.logger.warning("Log dosyası bulunamadı")
    
    def _show_help(self) -> None:
        """Yardım göster"""
        help_text = f"""
{Colors.BRIGHT_GREEN}VisualAim-Research - Yardım{Colors.RESET}

{Colors.BRIGHT_CYAN}Kontroller:{Colors.RESET}
  • Yön tuşları / W,S : Menüde gezinme
  • Enter / Space     : Seçimi onayla
  • ESC               : Üst menüye dön
  • Rakamlar (0-9)    : Doğrudan seçim

{Colors.BRIGHT_CYAN}Kısayollar:{Colors.RESET}
  • F1 : Bu yardım ekranı
  • F2 : Aimbot aç/kapat
  • F3 : Triggerbot aç/kapat
  • F4 : Programdan çık

{Colors.BRIGHT_CYAN}Profiller:{Colors.RESET}
  • STEALTH  : Güvenli, yavaş, insansı
  • BALANCED : Dengeli, önerilen
  • RAPID    : Hızlı, agresif
  • ULTRA    : Maksimum performans

{Colors.DIM}Not: Bu yazılım eğitim amaçlıdır.{Colors.RESET}
        """
        print(help_text)
        input("\nDevam etmek için Enter...")
    
    def _exit(self) -> None:
        """Programdan çık"""
        self.running = False
    
    def _get_aim_mode(self) -> str:
        """Mevcut aim modunu al"""
        return self.config.get('aim.mode').upper()
    
    # Public API
    def update_status(self, **kwargs) -> None:
        """Durum bilgilerini güncelle (ana döngüden çağrılabilir)"""
        self.status.update(kwargs)
        self._needs_redraw = True
    
    def run(self) -> None:
        """Ana döngü"""
        self.logger.info("Menü sistemi başlatıldı")
        
        while self.running:
            try:
                # Sadece bir şey değiştiğinde çiz.
                # (Her döngüde cls + yeniden çizim ekranı sürekli titretiyor,
                #  input() istemlerini de siliyordu.)
                if self._needs_redraw:
                    self._draw_current_menu()

                # Non-blocking input
                import msvcrt
                if msvcrt.kbhit():
                    char = msvcrt.getch()
                    # Özel tuşlar: oklar b'\xe0', fonksiyon tuşları b'\x00' ön ekiyle gelir
                    if char in (b'\xe0', b'\x00'):
                        char = msvcrt.getch()
                        if char == b'H':  # Up
                            self.selected_index = max(0, self.selected_index - 1)
                        elif char == b'P':  # Down
                            current_menu = self.menus.get(self.current_state, self.main_menu)
                            self.selected_index = min(len(current_menu) - 1, self.selected_index + 1)
                        elif char == b';':  # F1 - yardım
                            # Global kanca kapalıyken de çalışsın
                            self._show_help()
                    else:
                        try:
                            self._handle_input(char.decode('utf-8', errors='ignore'))
                        except Exception as e:
                            self.logger.error(f"Girdi hatası: {e}")
                    self._needs_redraw = True

                time.sleep(0.05)  # 20 FPS menu refresh

            except KeyboardInterrupt:
                self.running = False
            except Exception as e:
                self.logger.error(f"Menü hatası: {e}")
                self._needs_redraw = True
                time.sleep(0.5)  # hata döngüsünde CPU'yu doldurma
        
        self.logger.info("Menü sistemi kapatıldı")


# Test bloğu
if __name__ == "__main__":
    print("=" * 60)
    print("MENU SYSTEM TEST")
    print("=" * 60)
    
    # Mock config ve logger
    config = ConfigManager(auto_create=True, watch_changes=False)
    logger = get_logger(use_colors=True)
    
    # Menü oluştur
    menu = TerminalMenu(config=config, logger=logger)
    
    # Test durum güncellemesi
    menu.update_status(
        fps=144.0,
        latency_ms=5.2,
        target="Enemy_1",
        mode="SMOOTH"
    )
    
    # Çalıştır
    try:
        menu.run()
    except Exception as e:
        logger.error(f"Test hatası: {e}")
    
    print("\nTest tamamlandı") 
