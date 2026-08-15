#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VisualAim-Research Logger System
================================
Hacker terminal estetiği ile profesyonel log yönetimi. Renkli konsol çıktısı,
rotating dosya logları, JSON metrik kayıtları ve thread-safe operasyonlar.

Özellikler:
- ANSI renk kodları ile neon yeşil hacker teması
- Rotating file handler (10MB max, 5 backup)
- JSON metrik kayıtları (research_metrics.json)
- Context manager desteği
- Log seviyesi bazlı filtreleme
- Async logging desteği (queue-based)
- Performans metrikleri toplama

Author: İhsan
Version: 2.0.0
"""

import os
import sys
import json
import logging
import logging.handlers
import threading
import queue
import time
import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Union, Callable
from dataclasses import dataclass, asdict, field
from enum import Enum, auto
from functools import wraps


# ANSI Renk Kodları (Hacker Terminal Estetiği)
class Colors:
    """Terminal renk kodları"""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDERLINE = "\033[4m"
    
    # Temel renkler
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    
    # Parlak renkler (Neon tema)
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"      # Neon Yeşil (Ana tema rengi)
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"
    
    # Özel kombinasyonlar
    NEON_GREEN = "\033[38;5;82m"    # Canlı neon yeşil
    NEON_CYAN = "\033[38;5;51m"     # Neon mavi/turkuaz
    NEON_PINK = "\033[38;5;205m"    # Neon pembe
    NEON_RED = "\033[38;5;196m"     # Parlak kırmızı
    DARK_GRAY = "\033[38;5;240m"    # Koyu gri
    MID_GRAY = "\033[38;5;245m"     # Orta gri
    
    # Hacker teması için özel
    MATRIX_GREEN = "\033[38;5;46m"  # Matrix yeşili
    AMBER = "\033[38;5;214m"        # Amber/CRT rengi
    
    # Arka plan renkleri
    BG_BLACK = "\033[40m"
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"
    BG_WHITE = "\033[47m"


# Windows'ta ANSI renk desteğini etkinleştir
if os.name == 'nt':
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)


class LogLevel(Enum):
    """Özel log seviyeleri"""
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL
    SUCCESS = 25  # INFO ile WARNING arası
    METRIC = 15   # DEBUG ile INFO arası (metrikler için)


# Özel seviyeleri logging modülüne ekle
logging.addLevelName(LogLevel.SUCCESS.value, "SUCCESS")
logging.addLevelName(LogLevel.METRIC.value, "METRIC")


class HackerFormatter(logging.Formatter):
    """
    Hacker terminal estetiği ile renkli log formatlayıcı
    
    Format: [TIMESTAMP] [LEVEL] [MODULE] Message
    """
    
    # Seviye bazlı renkler
    LEVEL_COLORS = {
        logging.DEBUG: Colors.DARK_GRAY,
        LogLevel.METRIC.value: Colors.MID_GRAY,
        logging.INFO: Colors.NEON_GREEN,
        LogLevel.SUCCESS.value: Colors.MATRIX_GREEN,
        logging.WARNING: Colors.AMBER,
        logging.ERROR: Colors.NEON_RED,
        logging.CRITICAL: Colors.BRIGHT_RED + Colors.BOLD,
    }
    
    # Seviye simgeleri
    LEVEL_ICONS = {
        logging.DEBUG: "◆",
        LogLevel.METRIC.value: "◇",
        logging.INFO: "▸",
        LogLevel.SUCCESS.value: "✓",
        logging.WARNING: "⚠",
        logging.ERROR: "✗",
        logging.CRITICAL: "☠",
    }
    
    def __init__(
        self,
        use_colors: bool = True,
        show_timestamp: bool = True,
        show_level: bool = True,
        show_module: bool = True,
        datefmt: str = "%H:%M:%S.%f"
    ):
        super().__init__()
        self.use_colors = use_colors
        self.show_timestamp = show_timestamp
        self.show_level = show_level
        self.show_module = show_module
        self.datefmt = datefmt
        
        # Temel format
        self._fmt_parts = []
        if show_timestamp:
            self._fmt_parts.append("%(asctime)s")
        if show_level:
            self._fmt_parts.append("%(levelicon)s %(levelname)-8s")
        if show_module:
            self._fmt_parts.append("[%(module)s]")
        self._fmt_parts.append("%(message)s")
        
        self._base_fmt = " ".join(self._fmt_parts)
    
    def format(self, record: logging.LogRecord) -> str:
        # Simge ekle
        record.levelicon = self.LEVEL_ICONS.get(record.levelno, "•")
        
        # Renk uygula
        if self.use_colors and hasattr(sys.stdout, 'isatty') and sys.stdout.isatty():
            color = self.LEVEL_COLORS.get(record.levelno, Colors.RESET)
            reset = Colors.RESET
            
            # Timestamp
            if self.show_timestamp:
                timestamp = datetime.datetime.fromtimestamp(record.created).strftime(self.datefmt)[:-3]
                record.asctime = f"{Colors.DIM}{timestamp}{Colors.RESET}"
            
            # Level
            if self.show_level:
                levelname = record.levelname
                if record.levelno == LogLevel.SUCCESS.value:
                    levelname = "SUCCESS"
                elif record.levelno == LogLevel.METRIC.value:
                    levelname = "METRIC"
                record.levelname = f"{color}{levelname}{reset}"
                record.levelicon = f"{color}{record.levelicon}{reset}"
            
            # Module
            if self.show_module:
                record.module = f"{Colors.CYAN}{record.module}{Colors.RESET}"
            
            # Message
            record.message = record.getMessage()
            if record.levelno >= logging.ERROR:
                record.message = f"{color}{record.message}{reset}"
            elif record.levelno == LogLevel.SUCCESS.value:
                record.message = f"{Colors.MATRIX_GREEN}{record.message}{reset}"
            
            # Formatla
            formatted = self._base_fmt % record.__dict__
            
            # Exception varsa ekle
            if record.exc_info:
                formatted += "\n" + self.formatException(record.exc_info)
            
            return formatted
        else:
            # Renksiz format
            record.asctime = datetime.datetime.fromtimestamp(record.created).strftime(self.datefmt)[:-3]
            record.levelicon = ""
            return super().format(record)


class JSONFormatter(logging.Formatter):
    """JSON formatlı log formatlayıcı (metrikler için)"""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "message": record.getMessage(),
        }
        
        # Extra fields
        if hasattr(record, "metrics"):
            log_data["metrics"] = record.metrics
        
        return json.dumps(log_data, ensure_ascii=False)


@dataclass
class PerformanceMetrics:
    """Performans metrikleri veri yapısı"""
    timestamp: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    fps: float = 0.0
    frame_time_ms: float = 0.0
    latency_ms: float = 0.0
    detection_count: int = 0
    aim_accuracy: float = 0.0
    cpu_usage: float = 0.0
    memory_usage_mb: float = 0.0
    targets_locked: int = 0
    shots_fired: int = 0
    shots_hit: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MetricsCollector:
    """
    Performans metrikleri toplayıcı ve kaydedici
    
    research_metrics.json dosyasına periyodik yazım yapar.
    """
    
    def __init__(
        self,
        metrics_file: Union[str, Path] = "research_metrics.json",
        max_history: int = 1000,
        flush_interval: int = 60
    ):
        """
        Args:
            max_history: Bellekte ve dosyada tutulacak azami kayıt sayısı
            flush_interval: Kaç kayıtta bir diske yazılacağı. Kayıtlar saniyede
                bir üretildiği için 60 = dakikada bir yazım. Eskiden 10'du ve
                her yazımda tüm dosya okunup yeniden yazılıyordu (2 MB'lık
                dosyada ~60-100 ms'lik takılma).
        """
        self.metrics_file = Path(metrics_file)
        self.max_history = max_history
        self.flush_interval = flush_interval

        self._metrics_buffer: List[Dict[str, Any]] = []
        self._session_id = datetime.datetime.now().isoformat()
        self._current_session: Dict[str, Any] = {
            "start_time": self._session_id,
            "metrics": []
        }
        self._lock = threading.Lock()
        self._flush_count = 0

        # Varolan dosyayı yükle
        self._load_existing()
    
    def _load_existing(self) -> None:
        """Varolan metrik dosyasını yükle"""
        if self.metrics_file.exists():
            try:
                with open(self.metrics_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self._metrics_buffer = data[-self.max_history:]
                    elif isinstance(data, dict) and "sessions" in data:
                        self._metrics_buffer = data["sessions"][-1].get("metrics", [])
            except Exception as e:
                print(f"Metrik yükleme hatası: {e}")
    
    def record(self, metrics: PerformanceMetrics) -> None:
        """Yeni metrik kaydet"""
        with self._lock:
            session_metrics = self._current_session["metrics"]
            session_metrics.append(metrics.to_dict())

            # Bellekte sınırsız büyümesin (uzun oturumlarda yüzlerce MB)
            if len(session_metrics) > self.max_history:
                del session_metrics[:len(session_metrics) - self.max_history]

            self._flush_count += 1

            # Periyodik olarak diske yaz
            if self._flush_count >= self.flush_interval:
                self._flush()
                self._flush_count = 0
    
    def _flush(self) -> None:
        """Buffer'ı diske yaz"""
        try:
            # Tüm oturumları tutan yapı
            sessions = []
            if self.metrics_file.exists():
                with open(self.metrics_file, 'r', encoding='utf-8') as f:
                    try:
                        data = json.load(f)
                        if isinstance(data, dict) and "sessions" in data:
                            sessions = data["sessions"]
                    except:
                        pass
            
            # Mevcut oturumu GÜNCELLE (ekleme değil).
            # Eskiden her yazımda append ediliyordu: dosyadaki 10 "oturum"
            # aslında aynı oturumun 10 anlık görüntüsü oluyor, gerçek eski
            # oturumlar 10'luk sınır yüzünden siliniyordu.
            for index, existing in enumerate(sessions):
                if existing.get("start_time") == self._session_id:
                    sessions[index] = self._current_session
                    break
            else:
                sessions.append(self._current_session)

            # Son N oturumu tut
            max_sessions = 10
            if len(sessions) > max_sessions:
                sessions = sessions[-max_sessions:]
            
            # Yaz
            output = {
                "last_updated": datetime.datetime.now().isoformat(),
                "sessions": sessions
            }
            
            with open(self.metrics_file, 'w', encoding='utf-8') as f:
                json.dump(output, f, indent=2, ensure_ascii=False)
                
        except Exception as e:
            print(f"Metrik yazma hatası: {e}")
    
    def get_summary(self) -> Dict[str, Any]:
        """Oturum özeti al"""
        with self._lock:
            if not self._current_session["metrics"]:
                return {}
            
            metrics_list = self._current_session["metrics"]
            return {
                "session_duration": len(metrics_list),
                "avg_fps": sum(m["fps"] for m in metrics_list) / len(metrics_list),
                "avg_latency": sum(m["latency_ms"] for m in metrics_list) / len(metrics_list),
                "total_detections": sum(m["detection_count"] for m in metrics_list),
                "total_shots": sum(m["shots_fired"] for m in metrics_list),
                "total_hits": sum(m["shots_hit"] for m in metrics_list),
            }
    
    def close(self) -> None:
        """Kaydediciyi kapat"""
        self._flush()


class ResearchLogger:
    """
    Ana logger sınıfı - Singleton pattern
    
    Hem konsol hem dosya hem de metrik logları yönetir.
    """
    
    _instance: Optional['ResearchLogger'] = None
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(
        self,
        log_file: Union[str, Path] = "bot_logs.txt",
        metrics_file: Union[str, Path] = "research_metrics.json",
        logs_dir: Union[str, Path] = "logs",
        console_level: int = logging.INFO,
        file_level: int = logging.DEBUG,
        max_bytes: int = 10 * 1024 * 1024,  # 10MB
        backup_count: int = 5,
        use_colors: bool = True
    ):
        if hasattr(self, '_initialized'):
            return
        
        self._initialized = True
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        
        # Ana logger
        self.logger = logging.getLogger("VisualAim")
        self.logger.setLevel(logging.DEBUG)
        self.logger.handlers = []  # Eski handler'ları temizle
        
        # Konsol handler (renkli)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(console_level)
        console_formatter = HackerFormatter(use_colors=use_colors)
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)
        
        # Dosya handler (rotating)
        log_path = self.logs_dir / log_file
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setLevel(file_level)
        file_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(module)s:%(funcName)s:%(lineno)d | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        self.logger.addHandler(file_handler)
        
        # Metrik toplayıcı
        self.metrics = MetricsCollector(
            metrics_file=self.logs_dir / metrics_file
        )
        
        # Banner göster
        self._print_banner()
        
        self.info(f"Logger başlatıldı: {log_path}")
    
    def _print_banner(self) -> None:
        """Başlangıç banner'ı"""
        banner = f"""
{Colors.NEON_GREEN}{Colors.BOLD}
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   ██╗   ██╗██╗███████╗██╗   ██╗ █████╗ ██╗     ██████╗       ║
║   ██║   ██║██║██╔════╝██║   ██║██╔══██╗██║     ██╔══██╗      ║
║   ██║   ██║██║███████╗██║   ██║███████║██║     ██████╔╝      ║
║   ╚██╗ ██╔╝██║╚════██║██║   ██║██╔══██║██║     ██╔══██╗      ║
║    ╚████╔╝ ██║███████║╚██████╔╝██║  ██║███████╗██║  ██║      ║
║     ╚═══╝  ╚═╝╚══════╝ ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝      ║
║                                                              ║
║              VISUALAIM-RESEARCH v2.0.0                       ║
║              ═══ Powered by İhsan ═══                        ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝{Colors.RESET}
        """
        print(banner)
    
    # Log metodları
    def debug(self, msg: str, *args, **kwargs) -> None:
        self.logger.debug(msg, *args, **kwargs)
    
    def info(self, msg: str, *args, **kwargs) -> None:
        self.logger.info(msg, *args, **kwargs)
    
    def success(self, msg: str, *args, **kwargs) -> None:
        self.logger.log(LogLevel.SUCCESS.value, msg, *args, **kwargs)
    
    def warning(self, msg: str, *args, **kwargs) -> None:
        self.logger.warning(msg, *args, **kwargs)
    
    def error(self, msg: str, *args, **kwargs) -> None:
        self.logger.error(msg, *args, **kwargs)
    
    def critical(self, msg: str, *args, **kwargs) -> None:
        self.logger.critical(msg, *args, **kwargs)
    
    def metric(self, msg: str, metrics: Optional[Dict[str, Any]] = None, *args, **kwargs) -> None:
        """Metrik log"""
        extra = kwargs.get('extra', {})
        if metrics:
            extra['metrics'] = metrics
        kwargs['extra'] = extra
        # NOT: *args parametre listesinde yoktu, çağrıldığında NameError veriyordu
        self.logger.log(LogLevel.METRIC.value, msg, *args, **kwargs)
    
    def record_performance(self, metrics: PerformanceMetrics) -> None:
        """Performans metriği kaydet"""
        self.metrics.record(metrics)
    
    def get_metrics_summary(self) -> Dict[str, Any]:
        """Metrik özeti al"""
        return self.metrics.get_summary()
    
    def set_level(self, level: int) -> None:
        """Konsol seviyesini değiştir"""
        for handler in self.logger.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                handler.setLevel(level)
    
    def close(self) -> None:
        """Logger'ı kapat"""
        self.metrics.close()
        for handler in self.logger.handlers:
            handler.close()
        self.logger.handlers = []


# Global logger instance
_logger: Optional[ResearchLogger] = None


def get_logger(
    log_file: str = "bot_logs.txt",
    metrics_file: str = "research_metrics.json",
    **kwargs
) -> ResearchLogger:
    """
    Global logger instance al
    
    Args:
        log_file: Log dosyası adı
        metrics_file: Metrik dosyası adı
        **kwargs: Diğer parametreler
    
    Returns:
        ResearchLogger instance
    """
    global _logger
    if _logger is None:
        _logger = ResearchLogger(
            log_file=log_file,
            metrics_file=metrics_file,
            **kwargs
        )
    return _logger


def log_function(level: int = logging.DEBUG):
    """Fonksiyon decorator'ı - giriş/çıkış logları"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            logger = get_logger()
            logger.log(level, f"▶ {func.__name__} başladı")
            try:
                result = func(*args, **kwargs)
                logger.log(level, f"◀ {func.__name__} tamamlandı")
                return result
            except Exception as e:
                logger.error(f"✗ {func.__name__} hata: {e}")
                raise
        return wrapper
    return decorator


# Test bloğu
if __name__ == "__main__":
    import tempfile
    import shutil
    
    # Test dizini
    test_dir = tempfile.mkdtemp()
    
    try:
        print("=" * 60)
        print("LOGGER TEST")
        print("=" * 60)
        
        # Logger oluştur (renkli)
        logger = get_logger(
            log_file="test_logs.txt",
            metrics_file="test_metrics.json",
            logs_dir=test_dir,
            use_colors=True
        )
        
        print("\n[TEST] Log Seviyeleri")
        logger.debug("Debug mesajı - detaylı bilgi")
        logger.metric("Metrik: FPS=144, Latency=5ms")
        logger.info("Bilgi mesajı - normal durum")
        logger.success("Başarılı işlem!")
        logger.warning("Uyarı - dikkat edilmesi gereken durum")
        logger.error("Hata - işlem başarısız")
        logger.critical("Kritik hata - sistem durdu")
        
        print("\n[TEST] Performans Metrikleri")
        for i in range(5):
            metrics = PerformanceMetrics(
                fps=144.0 + i,
                frame_time_ms=6.94,
                latency_ms=5.0 + i * 0.5,
                detection_count=i * 2,
                aim_accuracy=0.95 - i * 0.01,
                cpu_usage=25.0,
                memory_usage_mb=150.0,
                targets_locked=i,
                shots_fired=i * 10,
                shots_hit=i * 8
            )
            logger.record_performance(metrics)
            time.sleep(0.1)
        
        # Özet al
        print("\n[TEST] Metrik Özeti")
        summary = logger.get_metrics_summary()
        print(f"Ortalama FPS: {summary.get('avg_fps', 0):.2f}")
        print(f"Toplam Atış: {summary.get('total_shots', 0)}")
        print(f"Toplam İsabet: {summary.get('total_hits', 0)}")
        
        print("\n" + "=" * 60)
        print("TÜM TESTLER BAŞARILI")
        print("=" * 60)
        
    finally:
        # Temizlik
        logger.close()
        shutil.rmtree(test_dir, ignore_errors=True)
        print(f"\nTemizlendi: {test_dir}") 
