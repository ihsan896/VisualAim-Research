#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VisualAim-Research: Main Coordinator
======================================
Projenin ana koordinatörü - tüm modülleri birleştirir.

Akış:
1. ConfigManager ile ayarları yükle
2. ScreenCapture başlat (DXGI öncelikli, MSS fallback)
3. ColorDetector, AimController, InputController, TriggerBot başlat
4. KalmanTracker ile hedef takip
5. HotkeyManager ile F2/F3/F4 dinleme
6. Ana döngü: capture -> detect -> aim -> trigger
7. Graceful shutdown: kaynakları serbest bırak

Yazar: İhsan
Versiyon: 1.0.0
"""

import sys
import time
import signal
import ctypes
import logging
import threading
from pathlib import Path
from typing import Optional, Tuple

# 🔧 KRİTİK EKSİK İMPORT - numpy HSV array'leri için gerekli
import numpy as np

# Oyun süreci tespiti (exe adı). Yoksa pencere başlığına düşülür.
try:
    import psutil
except ImportError:
    psutil = None

# Python 3.13 uyumluluğu
if sys.version_info < (3, 10):
    raise ImportError(f"Python {sys.version_info.major}.{sys.version_info.minor} desteklenmiyor. Python 3.10+ gerekli.")

# Windows konsol kodlaması (Türkçe karakterler için)
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except:
        pass

# =============================================================================
# MODÜL IMPORTLARI
# =============================================================================

# UI Modülleri
try:
    from ui.config_manager import ConfigManager
    from ui.logger import get_logger, PerformanceMetrics, Colors
    from ui.menu import TerminalMenu
except ImportError as e:
    print(f"[FATAL] UI modülleri yüklenemedi: {e}")
    sys.exit(1)

# Core Modülleri
try:
    from core.capture import ScreenCapture, CaptureMethod
    from core.detector import ColorDetector, Target
    from core.input_controller import InputController, MoveType
    from core.aim_controller import AimController, AimMode
    from core.kalman_tracker import KalmanTracker, TrackingState
    from core.trigger import TriggerBot, TriggerConfig, TriggerMode, TriggerWeapon
    from core.recoil import RecoilController, WeaponType
except ImportError as e:
    print(f"[FATAL] Core modülleri yüklenemedi: {e}")
    sys.exit(1)

# Yardımcı Modüller
try:
    from modules.performance_profiles import PerformanceProfiles, ProfileType
    from modules.anti_ban import Humanizer, HumanizationParams
    from modules.hotkey_manager import HotkeyManager, HotkeyAction
except ImportError as e:
    print(f"[FATAL] Yardımcı modüller yüklenemedi: {e}")
    sys.exit(1)


class VisualAimResearch:
    """
    VisualAim-Research Ana Koordinatörü
    
    Tüm alt sistemleri yöneten ve senkronize eden ana sınıf.
    """
    
    def __init__(self, enable_menu: bool = True):
        """
        Sistem başlatıcı

        Args:
            enable_menu: Terminal menüsünü aç (--no-menu ile kapatılabilir)
        """
        self.running = False
        self.shutdown_requested = False
        self.enable_menu = enable_menu
        
        # Logger (Singleton) - logs/ dizinine yazacak
        self.logger = get_logger(
            log_file="bot_logs.txt",
            metrics_file="research_metrics.json",
            logs_dir="logs",  # 🔧 logs/ alt dizini
            use_colors=True
        )
        
        self.logger.info("=" * 70)
        self.logger.info("VisualAim-Research v5.0 - Başlatılıyor")
        self.logger.info("Akademik Savunma Araştırması - Görsel Hedef Tespit Sistemi")
        self.logger.info("=" * 70)
        
        # Config Manager
        self.config_path = Path("research_config.ini")
        self.config = ConfigManager(
            config_path=self.config_path,
            profiles_dir="profiles",
            auto_create=True,
            watch_changes=False
        )
        
        # Performans Profilleri
        self.profiles = PerformanceProfiles(logger=self.logger)
        
        # Aktif profili uygula.
        # profile.auto_apply = false ise profil değerleri config'e YAZILMAZ:
        # performans profili her açılışta aim.speed, aim.smoothing,
        # trigger.delay_min/max, input.jitter_amount ve capture.target_fps
        # değerlerini eziyordu; elle yapılan ince ayarlar sessizce kayboluyordu.
        active_profile = self.config.get("profile.active", "balanced")
        auto_apply = bool(self.config.get("profile.auto_apply", True))
        profile_config = self.profiles.apply_profile(active_profile)

        if not auto_apply:
            self.logger.info(
                "[Main] profile.auto_apply = false - profil ve donanım optimizasyonu "
                "uygulanmadı, config'teki elle yapılmış ayarlar korunuyor"
            )
        
        # Intel Iris Xe optimizasyonu.
        # Önce uygulanmalı: sonuç profile_config'e yazılıyor ve aşağıdaki
        # döngüyle config'e işleniyor. (Daha önce config yazıldıktan SONRA
        # çağrılıyordu; dönen sözlük hiçbir yere gitmiyordu - ölü koddu.)
        if auto_apply:
            profile_config = self.profiles.optimize_for_intel_iris(profile_config)

            for key, value in profile_config.items():
                try:
                    self.config.set(key, value, save=False)
                except Exception as e:
                    self.logger.warning(f"[Config] Anahtar atlandı {key}: {e}")
            self.config.save()
        
        # Core modülleri başlat
        self._init_core_modules()
        
        # Yardımcı modülleri başlat
        self._init_helper_modules()
        
        # Hotkey Manager
        self.hotkey_manager = HotkeyManager(logger=self.logger)
        self._setup_hotkeys()
        
        # Terminal menüsü (ayrı thread'de çalışır)
        self.menu: Optional[TerminalMenu] = None
        self._menu_thread: Optional[threading.Thread] = None
        self._console_log_level = logging.INFO
        self._init_menu()

        # Oyun tespiti (exe adı birincil, pencere başlığı yedek)
        self.wait_for_game = bool(self.config.get("game.wait_for_window", True))
        self.use_process_check = bool(self.config.get("game.use_process_check", True)) and psutil is not None
        self.pause_when_closed = bool(self.config.get("game.pause_when_closed", True))

        self.game_titles = self._as_lower_list(self.config.get("game.window_titles", []))
        self.game_processes = set(self._as_lower_list(self.config.get("game.process_names", [])))

        self._game_active = False
        self._game_running = True
        self._game_check_time = 0.0
        self._proc_check_time = 0.0

        if self.wait_for_game:
            if self.use_process_check:
                self.logger.info(f"[Main] Oyun bekleniyor - exe: {sorted(self.game_processes)}")
            else:
                self.logger.info(f"[Main] Oyun bekleniyor - başlık: {self.game_titles}")
            if psutil is None:
                self.logger.warning(
                    "[Main] psutil yok - exe tespiti devre dışı, yalnızca pencere başlığı kullanılacak "
                    "(pip install psutil)"
                )

        # Tetik tuşu (config'de 0x06 gibi onaltılık yazılabilir)
        # Fallback string olmalı: "0x06" int'e çevrilemez, sayısal fallback
        # verilirse dönüştürme hatası loglanıyordu
        self.trigger_key_vk = self._parse_vk(self.config.get("trigger.activation_key", "0x06"))

        # Nişan noktası oranı (aim ve trigger aynı noktayı kullanmalı)
        self.head_offset = float(self.config.get("aim.head_offset", 0.43))

        # Hata ayıklama ayarları (config'de vardı, hiç okunmuyordu)
        self.debug_enabled = bool(self.config.get("debug.enabled", False))
        self.show_overlay = self.debug_enabled and bool(self.config.get("debug.show_overlay", False))
        self.save_screenshots = self.debug_enabled and bool(self.config.get("debug.save_screenshots", False))
        self.show_mask = bool(self.config.get("debug.show_mask", False))
        self.show_contours = bool(self.config.get("debug.show_contours", False))
        self.log_detections = self.debug_enabled and bool(self.config.get("debug.log_detections", False))
        self._overlay_ready = False
        self._last_frame = None
        if self.show_overlay:
            self.logger.warning(
                "[Main] Debug overlay AÇIK - her karede pencere çizimi yapılır, FPS düşer "
                "(debug.show_overlay = false ile kapatılır)"
            )

        # İstatistikler
        self.frame_count = 0
        self.detection_count = 0
        self.aim_move_count = 0
        self.empty_frame_count = 0
        self.last_fps_time = time.time()
        self.current_fps = 0.0
        self._target_acquired_at: Optional[float] = None
        self._reaction_delay = 0.0
        self._last_aim_moves = 0
        self._status_tick = 0
        self.total_frames = 0   # frame_count her saniye sıfırlanır, bu birikimli

        # Atış / isabet izleme (fire_async asenkron olduğu için sayaçla izlenir)
        self._last_shot_count = 0
        self._pending_hit_checks = 0
        self.shots_hit = 0
        self.recoil_comp_count = 0
        self._recoil_total_x = 0.0
        self._recoil_total_y = 0.0

        # Kalman dt takibi (gerçek kare süresinden beslenir)
        self._last_frame_time = 0.0
        self._frame_time_ema = 0.0
        self._kalman_dt = 0.0

        # Gecikme ve kaynak ölçümü (metriklerde hep 0 yazıyordu)
        self._process_latency_ms = 0.0
        self._process = None
        if psutil is not None:
            try:
                self._process = psutil.Process()
                self._process.cpu_percent()  # ilk çağrı referans alır
            except Exception:
                self._process = None
        
        self.logger.success("[Main] Tüm modüller başlatıldı")
        self.logger.info(f"[Main] Aktif Profil: {active_profile.upper()}")
        self.logger.info(f"[Main] Aim Modu: {self.aim_controller.mode.value}")
        self.logger.info(f"[Main] FOV Yarıçapı: {self.aim_controller.fov_radius}px")
    
    def _init_core_modules(self):
        """Çekirdek modülleri başlat"""
        
        # 1. Ekran Yakalama - backend artık config'ten seçiliyor
        # (capture.backend ayarı okunmuyor, her zaman AUTO kullanılıyordu)
        target_fps = self.config.get("capture.target_fps", 60)
        backend_name = str(self.config.get("capture.backend", "auto")).lower()
        try:
            backend = CaptureMethod(backend_name)
        except ValueError:
            self.logger.warning(f"[Main] Bilinmeyen backend '{backend_name}', AUTO kullanılıyor")
            backend = CaptureMethod.AUTO

        self.capture = ScreenCapture(
            capture_method=backend,
            target_fps=target_fps,
            buffer_size=3
        )

        # Yakalama alanı: tam ekran veya nişangâh çevresindeki bölge.
        # Tüm ekranı işlemek tespiti karede ~21-38 ms'ye çıkarır (FPS ~12);
        # aynı işlem 400x400 bölgede ~1.7 ms.
        full_screen = bool(self.config.get("capture.full_screen", False))

        if full_screen:
            self.capture.set_region(None)
            w, h = self.capture.get_dimensions()
            self.logger.warning(
                f"[Main] TAM EKRAN modu ({w}x{h}) - tespit maliyeti yüksek, "
                "FPS düşebilir (capture.full_screen = false ile bölge moduna dönülür)"
            )
        else:
            fov_x = self.config.get("capture.fov_x", 400)
            fov_y = self.config.get("capture.fov_y", 400)
            self.capture.set_center_region(
                width=fov_x, height=fov_y,
                offset_x=self.config.get("capture.offset_x", 0),
                offset_y=self.config.get("capture.offset_y", 0)
            )

        # Tespit öncesi küçültme (tam ekran taramasında maliyeti düşürür)
        self.detection_scale = float(self.config.get("capture.detection_scale", 1.0))
        if full_screen and self.detection_scale >= 1.0:
            self.logger.warning(
                "[Main] Tam ekran + detection_scale=1.0: tespit ~38 ms/kare sürer. "
                "capture.detection_scale = 0.25 ile ~2.5 ms'ye iner."
            )
        elif self.detection_scale < 1.0:
            self.logger.info(f"[Main] Tespit ölçeği: {self.detection_scale} (koordinatlar geri ölçekleniyor)")

        # Kare içi koordinatları ekran koordinatına çevirmek için
        self.capture_offset = self.capture.get_capture_offset()
        area = "tam ekran" if full_screen else f"bölge {self.config.get('capture.fov_x', 400)}x{self.config.get('capture.fov_y', 400)}"

        self.logger.info(
            f"[Main] ScreenCapture başlatıldı: {self.capture.get_backend_name()} @ {target_fps} FPS "
            f"| {area} @ {self.capture_offset}"
        )
        
        # 2. Renk Tespiti (HSV - Valorant kırmızı hedefler)
        # [color] bölümündeki filtre/morfoloji ayarları artık gerçekten
        # uygulanıyor (önceden çekirdekler detector içinde sabitti)
        self.detector = ColorDetector(
            min_area=self.config.get("color.min_blob_size", 30),
            max_area=self.config.get("color.max_blob_size", 5000),
            min_aspect=float(self.config.get("color.min_aspect_ratio", 0.25)),
            max_aspect=float(self.config.get("color.max_aspect_ratio", 4.0)),
            min_solidity=float(self.config.get("color.min_solidity", 0.0)),
            blur_kernel=(int(self.config.get("color.blur_kernel_x", 3)),
                         int(self.config.get("color.blur_kernel_y", 3))),
            morph_open_kernel=int(self.config.get("color.morph_open_kernel", 3)),
            morph_close_kernel=int(self.config.get("color.morph_close_kernel", 5)),
            morph_iterations=int(self.config.get("color.morph_iterations", 1))
        )
        
        # Renk aralıklarını config'den ayarla
        lower1, upper1 = self.config.get_color_range("")
        lower2, upper2 = self.config.get_color_range("2")
        
        # 🔧 np.array artık tanımlı (import numpy as np yapıldı)
        self.detector.set_color_range(
            np.array(lower1), np.array(upper1), range_id=1
        )
        self.detector.set_color_range(
            np.array(lower2), np.array(upper2), range_id=2
        )
        
        # 3. Fare Kontrolcüsü (WinAPI SendInput)
        self.input_controller = InputController(
            sensitivity=1.0  # Kalibrasyon sonrası ayarlanabilir
        )
        
        # 4. Aim Controller (FOV, Smooth/Snap/Hybrid)
        aim_config = {
            'aim_speed': self.config.get("aim.speed", 0.35),
            'smoothing': self.config.get("aim.smoothing", 0.15),
            'fov_radius': self.config.get("aim.fov_radius", 250),
            'head_offset': self.config.get("aim.head_offset", 0.43),
            'recoil_y': self.config.get("recoil.compensation_y", 1.0),
            'recoil_x': self.config.get("recoil.compensation_x", 1.0),
            'recoil_recovery': self.config.get("recoil.recovery_rate", 0.1),
            'aim_mode': self.config.get("aim.mode", "smooth"),
            'hybrid_threshold': self.config.get("aim.hybrid_threshold", 150)
        }
        self.aim_controller = AimController(aim_config, self.input_controller)

        # Tam ekran yakalanıyor ama FOV yarıçapı küçükse, ekranın büyük kısmı
        # yine de reddedilir - "tam ekran hareket" için ikisi birlikte gerekir.
        if full_screen:
            half_diag = int((self.capture.screen_width ** 2 + self.capture.screen_height ** 2) ** 0.5 / 2)
            if aim_config['fov_radius'] < half_diag:
                self.logger.warning(
                    f"[Main] aim.fov_radius = {aim_config['fov_radius']}px; bu yarıçapın dışındaki "
                    f"hedefler yok sayılır. Ekranın tamamında nişan için "
                    f"aim.fov_radius = {half_diag} yapılmalı."
                )
        
        # 5. Kalman Tracker (Hedef tahmini)
        use_kalman = self.config.get("aim.use_kalman", True)
        if use_kalman:
            # [kalman] bölümünün tamamı artık okunuyor (dördü koda gömülüydü)
            self.kalman = KalmanTracker(
                dt=1.0 / target_fps,
                process_noise=self.config.get("kalman.process_noise", 0.01),
                measurement_noise=self.config.get("kalman.measurement_noise", 0.1),
                max_prediction_frames=self.config.get("kalman.max_prediction_frames", 15),
                initial_covariance=float(self.config.get("kalman.initial_covariance", 1.0)) * 100.0,
                confidence_decay=float(self.config.get("kalman.confidence_decay", 0.95)),
                max_prediction_age=int(self.config.get("kalman.max_prediction_age", 10)),
                association_threshold=float(self.config.get("kalman.association_threshold", 200))
            )
        else:
            self.kalman = None
        
        # 6. TriggerBot (Otomatik ateşleme)
        trigger_config = TriggerConfig(
            threshold=self.config.get("trigger.threshold", 8),
            delay_min=self.config.get("trigger.delay_min", 10),
            delay_max=self.config.get("trigger.delay_max", 50),
            first_shot_delay=self.config.get("trigger.first_shot_delay", 0),
            mode=TriggerMode(self.config.get("trigger.mode", "hold_key")),
            weapon=TriggerWeapon(self.config.get("recoil.weapon", "vandal"))
        )
        self.trigger = TriggerBot(self.input_controller, trigger_config)
        
        # 7. Recoil Controller (Geri tepme kompanzasyonu)
        recoil_enabled = self.config.get("recoil.enabled", False)
        if recoil_enabled:
            self.recoil = RecoilController(
                weapon=WeaponType(self.config.get("recoil.weapon", "vandal"))
            )
            # [recoil] strength / randomization / max_offset da config'ten
            # (öncesinde compensation_factor 1.0 ve randomization 0.1 sabitti)
            self.recoil.set_compensation(float(self.config.get("recoil.strength", 1.0)))
            self.recoil.randomization = max(0.0, float(self.config.get("recoil.randomization", 0.1)))
            self.recoil.max_offset = float(self.config.get("recoil.max_offset", 100))
            self.logger.info(
                f"[Main] Recoil: {self.config.get('recoil.weapon', 'vandal')} "
                f"| güç {self.recoil.compensation_factor} "
                f"| rastgelelik {self.recoil.randomization} "
                f"| azami sapma {self.recoil.max_offset}px"
            )
        else:
            self.recoil = None
    
    def _init_helper_modules(self):
        """Yardımcı modülleri başlat"""
        
        # İnsan benzeri davranış (Humanizer)
        # [humanization] bölümü config'te vardı ama okunmuyordu; gecikmeler
        # koda gömülüydü (tepki 80-200 ms sabit). Artık ayarlanabilir:
        # reaction_delay_min/max = 0 vermek gecikmeyi tamamen kaldırır.
        reaction_min = int(self.config.get("humanization.reaction_delay_min", 80))
        reaction_max = int(self.config.get("humanization.reaction_delay_max", 200))
        click_min = int(self.config.get("humanization.click_delay_min", 50))
        click_max = int(self.config.get("humanization.click_delay_max", 150))

        humanizer_params = HumanizationParams(
            jitter_enabled=self.config.get("input.micro_jitter", True),
            jitter_min=float(self.config.get("input.jitter_min", 0.5)),
            jitter_max=self.config.get("input.jitter_amount", 1.0),
            delay_enabled=click_max > 0,
            delay_min_ms=click_min,
            delay_max_ms=max(click_min, click_max),
            curve_variation=float(self.config.get("input.curve_variation", 0.05)),
            reaction_delay_enabled=reaction_max > 0,
            reaction_min_ms=reaction_min,
            reaction_max_ms=max(reaction_min, reaction_max)
        )
        self.humanizer = Humanizer(params=humanizer_params, logger=self.logger)
        self.logger.info(
            f"[Main] Tepki gecikmesi: {reaction_min}-{reaction_max} ms "
            f"({'kapalı' if reaction_max <= 0 else 'açık'})"
        )
    
    @staticmethod
    def _as_lower_list(value) -> list:
        """Config'ten gelen liste/string değeri küçük harfli listeye çevir"""
        if isinstance(value, str):
            value = [value]
        return [str(v).strip().lower() for v in (value or []) if str(v).strip()]

    @staticmethod
    def _parse_vk(value) -> int:
        """Sanal tuş kodunu çöz ('0x06', '6', 6 -> 6)"""
        if isinstance(value, int):
            return value
        text = str(value).strip()
        try:
            return int(text, 16) if text.lower().startswith("0x") else int(text)
        except ValueError:
            return 0x06  # varsayılan: mouse4

    def _init_menu(self):
        """
        Terminal menüsünü hazırla

        Menü kendi başına F1-F4 global kancası kurmaz; aynı tuşlar
        HotkeyManager tarafından dinleniyor (çift tetikleme olurdu).
        Menü açılamazsa uygulama hotkey moduyla çalışmaya devam eder.
        """
        if not self.enable_menu:
            self.logger.info("[Main] Menü devre dışı (--no-menu)")
            return

        try:
            self.menu = TerminalMenu(
                config=self.config,
                logger=self.logger,
                global_hotkeys=False
            )
            self.menu.update_status(
                bot_running=True,  # döngü açılışta çalışır, [2] STOP BOT duraklatır
                profile=str(self.config.get("profile.active", "balanced")).upper(),
                mode=str(self.config.get("aim.mode", "smooth")).upper(),
                aim_enabled=bool(self.config.get("aim.enabled", False)),
                trigger_enabled=bool(self.config.get("trigger.enabled", True)),
            )
            self.logger.success("[Main] Terminal menüsü hazır")
        except Exception as e:
            self.menu = None
            self.logger.warning(f"[Main] Menü başlatılamadı, hotkey moduna geçiliyor: {e}")

    def _run_menu(self):
        """Menü döngüsü (ayrı thread)"""
        try:
            self.menu.run()
        except Exception as e:
            self.logger.error(f"[Main] Menü hatası: {e}")
        finally:
            # Menüden çıkıldıysa ([0] EXIT / F4) uygulama da kapanmalı
            self.shutdown_requested = True

    def _setup_hotkeys(self):
        """Hotkey callback'lerini ayarla"""
        
        # ÖNEMLİ: HotkeyManager._handle_action() durumu ZATEN çeviriyor.
        # Bu callback'ler bir kez daha çevirdiği için F2/F6 net etkisi sıfırdı
        # ("Aim Layer hiçbir zaman aktif olmuyor"). Artık sadece okuyorlar.
        def on_aim_toggle():
            state = self.hotkey_manager.get_state('aim_enabled')
            self.config.set("aim.enabled", state, save=False)
            self.logger.success(f"[Hotkey] Aimbot: {'AKTİF' if state else 'PASİF'}")
            if self.menu:
                self.menu.update_status(aim_enabled=state)

        def on_recoil_toggle():
            state = self.hotkey_manager.get_state('recoil_enabled')
            self.config.set("recoil.enabled", state, save=False)
            self.logger.success(f"[Hotkey] Recoil: {'AKTİF' if state else 'PASİF'}")

        def on_trigger_toggle():
            # HotkeyManager durumu zaten çevirdi; config'e de yansıt ki
            # menü ve ana döngü aynı değeri görsün
            state = self.hotkey_manager.get_state('trigger_enabled')
            self.config.set("trigger.enabled", state, save=False)
            if self.menu:
                self.menu.update_status(trigger_enabled=state)

        def on_exit():
            self.logger.warning("[Hotkey] Çıkış talebi alındı (F4)")
            self.shutdown_requested = True

        self.hotkey_manager.register_callback(HotkeyAction.TOGGLE_AIM, on_aim_toggle)
        self.hotkey_manager.register_callback(HotkeyAction.TOGGLE_TRIGGER, on_trigger_toggle)
        self.hotkey_manager.register_callback(HotkeyAction.TOGGLE_RECOIL, on_recoil_toggle)
        self.hotkey_manager.register_callback(HotkeyAction.EXIT, on_exit)

        # Başlangıç durumlarını config'ten al.
        # aim.enabled = true yazılı olmasına rağmen kod hep False başlıyordu;
        # oyun açıldığında sistemin otomatik aktif olması için config esas alınır.
        for state_key, config_key in (('aim_enabled', 'aim.enabled'),
                                      ('trigger_enabled', 'trigger.enabled'),
                                      ('recoil_enabled', 'recoil.enabled')):
            self.hotkey_manager.set_state(state_key, bool(self.config.get(config_key, False)))

        self.logger.info(
            f"[Main] Başlangıç durumu - Aim: {'AÇIK' if self.hotkey_manager.get_state('aim_enabled') else 'KAPALI'}, "
            f"Trigger: {'AÇIK' if self.hotkey_manager.get_state('trigger_enabled') else 'KAPALI'}, "
            f"Recoil: {'AÇIK' if self.hotkey_manager.get_state('recoil_enabled') else 'KAPALI'}"
        )
    
    @staticmethod
    def _foreground_info() -> Tuple[str, str]:
        """
        Öndeki pencerenin (exe adı, başlık) bilgisi

        Exe adı pencere PID'sinden alınır: psutil.process_iter() ile tüm
        süreçleri taramak ~37 ms sürerken bu yol ~0.03 ms.

        Returns:
            (exe_adi, baslik) - alınamayan alan boş string
        """
        exe = ""
        title = ""
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()

            length = user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value

            if psutil is not None:
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value:
                    exe = psutil.Process(pid.value).name()
        except Exception:
            pass

        return exe, title

    def _is_game_running(self) -> bool:
        """
        Oyun süreci çalışıyor mu (pencere önde olmasa bile)

        psutil.process_iter() pahalı (~37 ms), bu yüzden 2 saniyede bir
        çalışır ve sonucu önbelleğe alır.
        """
        if psutil is None or not self.pause_when_closed or not self.game_processes:
            return True

        now = time.perf_counter()
        if now - self._proc_check_time < 2.0:
            return self._game_running

        self._proc_check_time = now

        try:
            running = any(
                (p.info['name'] or "").lower() in self.game_processes
                for p in psutil.process_iter(['name'])
            )
        except Exception as e:
            self.logger.warning(f"[Main] Süreç taraması başarısız: {e}")
            return True

        if running != self._game_running:
            if running:
                self.logger.success("[Main] Oyun süreci başlatıldı")
            else:
                self.logger.info("[Main] Oyun kapandı - bekleme modunda")
            self._game_running = running

        return running

    def _is_game_active(self) -> bool:
        """
        Oyun aktif mi? (süreç çalışıyor VE penceresi önde)

        Tespit sırası:
          1. Öndeki pencerenin EXE adı (birincil - başlık değişse de çalışır)
          2. Pencere başlığı (yedek - exe okunamazsa)

        Kontrol saniyede ~4 kez yapılır, aradaki karelerde önbellek kullanılır.

        Returns:
            True: işleme devam edilebilir
        """
        if not self.wait_for_game:
            return True

        # Oyun hiç çalışmıyorsa pencereye bakmaya gerek yok
        if not self._is_game_running():
            if self._game_active:
                self._game_active = False
                if self.menu:
                    self.menu.update_status(target='OYUN KAPALI')
            return False

        now = time.perf_counter()
        if now - self._game_check_time < 0.25:
            return self._game_active

        self._game_check_time = now

        exe, title = self._foreground_info()

        # 1. Exe adı (birincil)
        active = False
        matched_by = ""
        if self.use_process_check and exe and self.game_processes:
            active = exe.lower() in self.game_processes
            if active:
                matched_by = f"exe={exe}"

        # 2. Pencere başlığı (yedek)
        if not active and self.game_titles:
            lowered = title.lower()
            if any(t and t in lowered for t in self.game_titles):
                active = True
                matched_by = f"başlık={title}"

        # Durum değişiminde tek sefer log
        if active != self._game_active:
            if active:
                self.logger.success(f"[Main] Oyun penceresi aktif ({matched_by})")
            else:
                self.logger.info(
                    f"[Main] Oyun öne gelene kadar bekleniyor "
                    f"(önde: {exe or title or 'bilinmiyor'})"
                )
            self._game_active = active
            if self.menu:
                self.menu.update_status(target='--' if active else 'OYUN BEKLENIYOR')

        return active

    def _trigger_allowed(self) -> bool:
        """Tetik modu şu an ateşlemeye izin veriyor mu (mod mantığı TriggerBot'ta)"""
        return self.trigger.mode_allows_fire(
            activation_key=self.trigger_key_vk,
            toggle_state=self.hotkey_manager.get_state('trigger_enabled')
        )

    def _draw_debug_overlay(self, frame, targets, chosen, local_center, mask=None) -> None:
        """
        Tespit sonucunu ayrı bir pencerede göster (config: debug.show_overlay)

        Tüm hedefler ince, seçilen hedef kalın çerçeveyle ve nişan noktasıyla
        çizilir. debug.show_mask açıksa kare yerine ikili maske gösterilir,
        debug.show_contours açıksa kontur sınırları da çizilir.
        Pencere kapatılırsa overlay kendini devre dışı bırakır.
        """
        try:
            import cv2

            # show_mask: ham maskeyi göster (renk aralığı ayarlarken faydalı)
            if self.show_mask and mask is not None and mask.size:
                view = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
                if view.shape[:2] != frame.shape[:2]:
                    view = cv2.resize(view, (frame.shape[1], frame.shape[0]),
                                      interpolation=cv2.INTER_NEAREST)
            else:
                view = frame.copy()

            if self.show_contours:
                for t in targets:
                    cv2.rectangle(view, (t.x, t.y), (t.x + t.width, t.y + t.height),
                                  (255, 0, 255), 1)

            for t in targets:
                colour = (0, 255, 0) if t is chosen else (120, 120, 120)
                thickness = 2 if t is chosen else 1
                cv2.rectangle(view, (t.x, t.y),
                              (t.x + t.width, t.y + t.height), colour, thickness)
                if t is chosen:
                    cv2.drawMarker(view, (t.head_x, self._head_y(t)),
                                   (0, 0, 255), cv2.MARKER_CROSS, 14, 2)

            cv2.drawMarker(view, local_center, (0, 255, 255), cv2.MARKER_CROSS, 18, 1)
            cv2.putText(view, f"FPS {self.current_fps:.0f}  hedef {len(targets)}",
                        (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)

            cv2.imshow("VisualAim - Debug", view)
            self._overlay_ready = True
            cv2.waitKey(1)
        except Exception as e:
            self.logger.warning(f"[Main] Overlay kapatıldı: {e}")
            self.show_overlay = False

    def _save_error_screenshot(self, reason: str) -> None:
        """Hata anındaki kareyi diske yaz (config: debug.save_screenshots)"""
        if not self.save_screenshots or self._last_frame is None:
            return
        try:
            import cv2

            shots_dir = Path("logs") / "screenshots"
            shots_dir.mkdir(parents=True, exist_ok=True)
            name = time.strftime("hata_%Y%m%d_%H%M%S.png")
            path = shots_dir / name
            cv2.imwrite(str(path), self._last_frame)
            self.logger.warning(f"[Main] Hata ekran görüntüsü kaydedildi: {path} ({reason})")
        except Exception as e:
            self.logger.error(f"[Main] Ekran görüntüsü kaydedilemedi: {e}")

    def _apply_recoil_compensation(self, shot_count: int) -> None:
        """
        Atılan mermiler için silaha özel geri tepme telafisini fareye uygula

        RecoilController.on_shot_fired() mermi sırasına göre paternden telafi
        döndürüyordu (Vandal: 0,-15 / 0,-22 / 0,-25 ...) ama dönüş değeri
        kullanılmıyordu; onun yerine AimController'a sabit 1 piksel ekleniyor
        ve patern sistemi tamamen atıl kalıyordu.

        Args:
            shot_count: Son karede gerçekleşen atış sayısı
        """
        if not self.recoil or not self.hotkey_manager.get_state('recoil_enabled'):
            return

        # Seri bittiyse (RecoilController mermi sayacını sıfırladı) birikimi sıfırla
        if self.recoil.shot_count == 0:
            self._recoil_total_x = 0.0
            self._recoil_total_y = 0.0

        total_x = 0.0
        total_y = 0.0
        for _ in range(shot_count):
            comp_x, comp_y = self.recoil.on_shot_fired()
            total_x += comp_x
            total_y += comp_y

        # recoil.max_offset: bir seri boyunca uygulanan toplam telafi sınırı.
        # Sınırsız bırakılırsa uzun spreylerde nişangâh ekrandan çıkabiliyor.
        limit = getattr(self.recoil, "max_offset", 0.0)
        if limit > 0:
            total_x = self._clamp_delta(self._recoil_total_x, total_x, limit)
            total_y = self._clamp_delta(self._recoil_total_y, total_y, limit)

        self._recoil_total_x += total_x
        self._recoil_total_y += total_y

        if total_x or total_y:
            # Telafi zaten ters yönde (comp = -recoil), doğrudan uygulanır
            self.input_controller.move_relative(total_x, total_y)
            self.recoil_comp_count += 1

    @staticmethod
    def _clamp_delta(current: float, delta: float, limit: float) -> float:
        """|current + delta| <= limit olacak şekilde delta'yı kırp"""
        target = current + delta
        if target > limit:
            return max(0.0, limit - current)
        if target < -limit:
            return min(0.0, -limit - current)
        return delta

    def _head_y(self, target) -> int:
        """
        Hedefin nişan alınacak Y koordinatı (kare içi)

        Target.head_y sabit %28 kullanıyor, AimController ise config'teki
        head_offset'i (0.43) kullanıyordu; aim ve trigger farklı noktalara
        bakıyordu. İkisi de artık aynı oranı kullanır.
        """
        return int(target.y + target.height * self.head_offset)

    def _process_frame(self) -> bool:
        """
        Tek kare işleme döngüsü

        Returns:
            bool: Başarılı ise True
        """
        # 0. Oyun penceresi kontrolü - oyun önde değilse hiçbir şey yapma
        if not self._is_game_active():
            time.sleep(0.05)
            return False

        # 1. Ekran yakala
        frame = self.capture.grab()
        if frame is None:
            # Sessizce devam etme: boş kareyi say ve periyodik olarak bildir
            self.empty_frame_count += 1
            if self.empty_frame_count % 120 == 0:
                stats = self.capture.get_capture_stats()
                self.logger.warning(
                    f"[Main] {self.empty_frame_count} boş kare "
                    f"(backend={stats['backend']}, hata={stats['consecutive_errors']})"
                )
            return False

        self.frame_count += 1
        self.total_frames += 1

        # 1b. Gerçek kare süresini ölç ve Kalman'a bildir.
        # dt sabit 1/target_fps veriliyordu; gerçek kare süresi bundan
        # saptığında filtre hızları yanlış ölçekte tahmin ediyordu.
        now_perf = time.perf_counter()
        if self._last_frame_time > 0.0:
            dt = now_perf - self._last_frame_time
            # EMA ile yumuşat (tek karelik sıçramalar filtreyi bozmasın)
            self._frame_time_ema = (0.9 * self._frame_time_ema + 0.1 * dt
                                    if self._frame_time_ema > 0 else dt)
            if self.kalman and abs(self._frame_time_ema - self._kalman_dt) > 0.002:
                # 5-500 FPS aralığına kırp: duraklamalar dt'yi uçurmasın
                fps = max(5.0, min(500.0, 1.0 / max(self._frame_time_ema, 1e-6)))
                self.kalman.set_dt(fps)
                self._kalman_dt = 1.0 / fps
        self._last_frame_time = now_perf

        # 2. Hedef tespiti yap
        # Yakalama bölgesi kullanıldığı için nişangâh kare içinde merkeze denk
        # gelir; ekran koordinatına çevirmek capture_offset ile yapılır.
        screen_center = self.capture.get_screen_center()
        local_center = self.capture.get_local_center()
        targets, _mask = self.detector.detect(frame, local_center, scale=self.detection_scale)

        if targets:
            self.detection_count += 1
            # debug.log_detections: her tespiti dosyaya yaz (ayıklama için)
            if self.log_detections:
                first = targets[0]
                self.logger.debug(
                    f"[Tespit] {len(targets)} hedef | en yakın: "
                    f"({first.center_x},{first.center_y}) {first.width}x{first.height} "
                    f"güven={first.confidence:.2f} mesafe={first.distance_from_center:.0f}px"
                )

        # 3. Kalman Tracker ile hedef takip/tahmin
        tracked_target = None
        if self.kalman:
            tracked_target = self.kalman.process(targets)

        # Kullanılacak hedef (Kalman tahmini veya doğrudan tespit)
        if tracked_target and tracked_target.is_reliable:
            target = tracked_target.target
        elif targets:
            target = targets[0]  # En yakın hedef
        else:
            target = None

        # 4. Aimbot (eğer aktifse)
        if self.hotkey_manager.get_state('aim_enabled') and target:
            # Reaksiyon gecikmesi: hedef YENİ görüldüğünde bir kez uygulanır.
            # Eskiden her karede sleep(80-200ms) çağrılıyor, döngü ~10 FPS'e
            # düşüyordu. Artık bloklamadan, süre dolana kadar aim ertelenir.
            now = time.perf_counter()
            if self._target_acquired_at is None:
                self._target_acquired_at = now
                self._reaction_delay = self.humanizer.get_reaction_delay()

            if now - self._target_acquired_at >= self._reaction_delay:
                # Aim hesapla ve uygula.
                # Hareket AimController içinde bir kez uygulanır; jitter
                # movement_filter ile araya girer. (Daha önce hem burada hem
                # AimController içinde hareket uygulanıyor, hedef 2x aşılıyordu.)
                move = self.aim_controller.calculate_movement(
                    target, screen_center,
                    capture_offset=self.capture_offset,
                    movement_filter=self.humanizer.apply_humanization
                )
                if move:
                    self.aim_move_count += 1
        else:
            self._target_acquired_at = None

        # 5. TriggerBot (eğer aktifse ve tetik modu izin veriyorsa)
        if target and self.config.get("trigger.enabled", True) and self._trigger_allowed():
            # Nişangâh ile hedef arasındaki mesafe ekran koordinatında ölçülür
            if tracked_target:
                tx, ty = tracked_target.aim_x, tracked_target.aim_y
            else:
                tx, ty = target.head_x, self._head_y(target)

            tx += self.capture_offset[0]
            ty += self.capture_offset[1]

            # Önceki karede atış yapıldıysa: hedef HÂLÂ nişangâhta mı?
            # (isabet yaklaşımı - oyun verisine erişim olmadığı için gerçek
            #  isabet bilinemez, nişangâhın hedefte kalması ölçülür)
            if self._pending_hit_checks > 0:
                if self.trigger.check_target(tx, ty, screen_center[0], screen_center[1]):
                    self.shots_hit += 1
                self._pending_hit_checks -= 1

            # fire_async: check_target'ı kendi içinde yapar ve ateşi AYRI
            # THREAD'de tetikler. fire() ana döngüde 20-60 ms uyuyordu.
            self.trigger.fire_async(tx, ty, screen_center[0], screen_center[1])

        # 5b. Ateş gerçekleşti mi? (fire_async asenkron olduğu için sayaç izlenir)
        new_shots = self.trigger.total_shots - self._last_shot_count
        if new_shots > 0:
            self._last_shot_count = self.trigger.total_shots
            self._pending_hit_checks += new_shots
            self._apply_recoil_compensation(new_shots)

        # 6. Recoil recovery (her frame'de azalt)
        if self.recoil and not self.trigger.is_on_target:
            self.recoil.on_stop_firing()

        # 6b. Debug overlay (config: debug.show_overlay)
        if self.show_overlay:
            self._draw_debug_overlay(frame, targets, target, local_center, _mask)

        # Hata anında ekran görüntüsü kaydedilebilsin diye son kare saklanır
        if self.save_screenshots:
            self._last_frame = frame

        # Kare alındıktan sonra tespit+nişan için harcanan süre
        self._process_latency_ms = (time.perf_counter() - now_perf) * 1000.0

        # 7. FPS hesapla
        current_time = time.time()
        if current_time - self.last_fps_time >= 1.0:
            self.current_fps = self.frame_count / (current_time - self.last_fps_time)
            self.frame_count = 0
            self.last_fps_time = current_time
            
            # Metrik kaydet (her saniye)
            if self.logger:
                shots = self.trigger.total_shots if self.trigger else 0
                metrics = PerformanceMetrics(
                    fps=self.current_fps,
                    frame_time_ms=self._frame_time_ema * 1000.0,
                    # Gerçek işlem gecikmesi: kare alındıktan sonra tespit +
                    # nişan için harcanan süre (kare süresinden farklı)
                    latency_ms=self._process_latency_ms,
                    detection_count=self.detection_count,
                    targets_locked=self.aim_move_count,
                    cpu_usage=self._process.cpu_percent() if self._process else 0.0,
                    memory_usage_mb=(self._process.memory_info().rss / 1048576.0
                                     if self._process else 0.0),
                    shots_fired=shots,
                    shots_hit=self.shots_hit,
                    aim_accuracy=(self.shots_hit / shots) if shots else 0.0
                )
                self.logger.record_performance(metrics)

            # Periyodik durum satırı: oyun tam ekranken menü görünmediği için
            # sistemin çalışıp çalışmadığı ancak logdan anlaşılabiliyor.
            self._status_tick += 1
            if self._status_tick % 5 == 0:
                stats = self.capture.get_capture_stats()
                shots = self.trigger.total_shots
                acc = (self.shots_hit / shots * 100) if shots else 0.0
                self.logger.info(
                    f"[Durum] FPS {self.current_fps:5.1f} ({self._process_latency_ms:.1f} ms işlem) "
                    f"| kare {self.total_frames} | hedefli kare {self.detection_count} "
                    f"| nişan {self.aim_move_count} | atış {shots} "
                    f"| isabet {self.shots_hit} (%{acc:.0f}) | recoil {self.recoil_comp_count} "
                    f"| backend {stats['backend']} | boş kare {stats['empty_frames']}"
                )

            # Tanılama: aim açık ama fare hiç oynamıyorsa sebebini yaz.
            # ("Aim Layer aktif olmuyor" durumunda hangi aşamada takıldığı
            #  loglardan görünsün diye.)
            aim_on = self.hotkey_manager.get_state('aim_enabled')
            if aim_on and self.aim_move_count == self._last_aim_moves:
                stats = self.aim_controller.stats
                self.logger.warning(
                    f"[Tanı] Aim AÇIK ama hareket yok | hedef görülen kare: {self.detection_count} "
                    f"| FOV dışı reddedilen: {stats.total_fov_rejects} "
                    f"| FOV yarıçapı: {self.aim_controller.fov_radius}px "
                    f"| boş kare: {self.empty_frame_count}"
                )
            self._last_aim_moves = self.aim_move_count

            # Menü durum çubuğunu tazele (saniyede bir)
            if self.menu:
                self.menu.update_status(
                    fps=self.current_fps,
                    latency_ms=1000.0 / self.current_fps if self.current_fps > 0 else 0.0,
                    aim_enabled=bool(self.hotkey_manager.get_state('aim_enabled')),
                    trigger_enabled=bool(self.config.get("trigger.enabled", True)),
                    mode=str(self.aim_controller.mode.value).upper(),
                    target='LOCKED' if target else '--',
                )

        return True
    
    def run(self):
        """Ana döngü"""
        self.running = True
        
        # Hotkey'leri başlat
        if not self.hotkey_manager.start():
            self.logger.error("[Main] HotkeyManager başlatılamadı - çıkılıyor")
            return
        
        self.logger.success("[Main] Sistem çalışıyor - F2: Aim, F3: Recoil, F4: Çıkış")

        # Graceful shutdown için signal handler
        # (signal.signal yalnızca ana thread'de çalışır; bu yüzden bot döngüsü
        #  ana thread'de kalır, menü ayrı thread'e alınır)
        def signal_handler(signum, frame):
            self.logger.warning(f"[Main] Signal {signum} alındı - kapatılıyor")
            self.shutdown_requested = True

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Menüyü başlat
        if self.menu:
            self._menu_thread = threading.Thread(
                target=self._run_menu, name="TerminalMenu", daemon=True
            )
            self._menu_thread.start()
            # Menü ekranını sürekli log akışı bozmasın; dosya logu etkilenmez.
            # Kullanıcı menüden (7 > 3) log seviyesini değiştirebilir.
            self.logger.set_level(logging.WARNING)

        # Ana döngü
        # Tek bir istisna tüm döngüyü bitiriyordu; artık hata sayılıyor ve
        # üst üste max_consecutive_errors kez tekrarlanana kadar toparlanıyor
        # (config: [advanced] max_consecutive_errors / error_recovery_delay).
        max_errors = int(self.config.get("advanced.max_consecutive_errors", 10))
        recovery_delay = float(self.config.get("advanced.error_recovery_delay", 5))
        consecutive_errors = 0

        try:
            while self.running and not self.shutdown_requested:
                # [1] START BOT / [2] STOP BOT: döngüyü duraklat
                if self.menu and not self.menu.status.get('bot_running', True):
                    time.sleep(0.05)
                    continue

                try:
                    self._process_frame()
                    consecutive_errors = 0
                except Exception as e:
                    consecutive_errors += 1
                    self.logger.error(
                        f"[Main] Kare işleme hatası ({consecutive_errors}/{max_errors}): {e}"
                    )
                    if consecutive_errors == 1:
                        import traceback
                        self.logger.debug(traceback.format_exc())
                        self._save_error_screenshot(str(e))

                    if consecutive_errors >= max_errors:
                        self.logger.critical(
                            f"[Main] {max_errors} ardışık hata - kapatılıyor"
                        )
                        break

                    time.sleep(recovery_delay)
                    continue

                # Küçük uyku (CPU kullanımını düşür)
                time.sleep(0.001)

        except Exception as e:
            self.logger.critical(f"[Main] Kritik hata: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            self._save_error_screenshot(str(e))
        
        finally:
            self.shutdown()
    
    def shutdown(self):
        """Graceful shutdown - tüm kaynakları serbest bırak"""
        # Konsol log seviyesini geri al (menü için WARNING'e çekilmişti)
        self.logger.set_level(self._console_log_level)
        self.logger.info("[Main] Kapatma işlemi başlatılıyor...")

        self.running = False

        # Menüyü durdur
        if self.menu:
            self.menu.running = False
            if self._menu_thread and self._menu_thread.is_alive():
                self._menu_thread.join(timeout=2)

        # Hotkey'leri durdur
        self.hotkey_manager.stop()
        
        # Debug penceresini kapat
        if self._overlay_ready:
            try:
                import cv2
                cv2.destroyAllWindows()
            except Exception:
                pass

        # Ekran yakalamayı kapat
        if hasattr(self, 'capture'):
            self.capture.close()
        
        # Logger'ı kapat
        if self.logger:
            summary = self.logger.get_metrics_summary()
            self.logger.info(f"[Main] Oturum özeti: {summary}")
            self.logger.close()
        
        print("\n[VisualAim-Research] Güvenli şekilde kapatıldı.")
        print("[VisualAim-Research] Akademik araştırma amaçlı geliştirilmiştir.")


def main():
    """Giriş noktası"""
    # --no-menu / --no-ui: menüsüz, sadece hotkey modu
    enable_menu = not any(arg in ('--no-menu', '--no-ui') for arg in sys.argv[1:])

    try:
        app = VisualAimResearch(enable_menu=enable_menu)
        app.run()
    except Exception as e:
        print(f"\n[FATAL] Uygulama başlatılamadı: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()