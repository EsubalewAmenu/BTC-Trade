import os
import sys
import time
from pathlib import Path


heartbeat = Path(os.getenv("DATA_DIR", "/app/data")) / "heartbeat"
poll_seconds = int(os.getenv("POLL_SECONDS", "10"))
maximum_age = max(180, poll_seconds * 6)

if not heartbeat.exists() or time.time() - heartbeat.stat().st_mtime > maximum_age:
    sys.exit(1)
