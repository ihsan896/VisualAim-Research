# init_logs.py - Bir kez çalıştır
import json
from pathlib import Path
from datetime import datetime

# Dizin yapısı - main.py'deki get_logger(logs_dir=...) ile aynı olmalı
LOGS_DIR = Path(".")
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Boş bot_logs.txt (logger rotating kullanacak)
(LOGS_DIR / "bot_logs.txt").touch()

# research_metrics.json başlangıç yapısı
metrics_data = {
    "last_updated": datetime.now().isoformat(),
    "sessions": [],
    "summary": {
        "total_sessions": 0,
        "total_runtime_seconds": 0,
        "avg_fps": 0,
        "total_detections": 0,
        "total_shots_fired": 0,
        "total_shots_hit": 0,
        "accuracy_rate": 0
    }
}

metrics_path = LOGS_DIR / "research_metrics.json"
if metrics_path.exists():
    print(f"[SKIP] Mevcut metrik dosyası korundu: {metrics_path.absolute()}")
    print("       Sıfırlamak istiyorsan dosyayı silip scripti tekrar çalıştır.")
else:
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics_data, f, indent=2, ensure_ascii=False)
    print(f"[OK] Metrik dosyası oluşturuldu: {metrics_path.absolute()}")

print(f"[OK] Logs dizini hazırlandı: {LOGS_DIR.absolute()}")
