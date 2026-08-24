import os
import sys
import time
from pathlib import Path


heartbeat = Path(os.getenv("DATA_DIR", "/app/data")) / "heartbeat"
interval = os.getenv("TRADE_INTERVAL", "15m").strip().lower()
number = int(interval[:-1])
interval_seconds = number * (60 if interval[-1] == "m" else 3600)
close_delay = int(os.getenv("CANDLE_CLOSE_DELAY_SECONDS", "20"))
maximum_age = max(180, interval_seconds + close_delay + 180)

if not heartbeat.exists() or time.time() - heartbeat.stat().st_mtime > maximum_age:
    sys.exit(1)
