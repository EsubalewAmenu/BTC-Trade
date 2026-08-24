import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "app"))

from settings import Settings, _interval_seconds, seconds_until_candle_request


class SettingsTests(unittest.TestCase):
    def test_interval_seconds(self):
        self.assertEqual(_interval_seconds("15m"), 900)
        self.assertEqual(_interval_seconds("1h"), 3600)

    def test_request_is_scheduled_twenty_seconds_after_next_close(self):
        now = datetime(2026, 8, 24, 14, 16, 0, tzinfo=timezone.utc)
        delay = seconds_until_candle_request(
            "2026-08-24T14:14:59.999000+00:00", "15m", 20, now
        )
        self.assertAlmostEqual(delay, 14 * 60 + 19.999, places=3)

    def test_stale_state_requests_immediately(self):
        now = datetime(2026, 8, 24, 15, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(
            seconds_until_candle_request(
                "2026-08-24T14:14:59.999000+00:00", "15m", 20, now
            ),
            0,
        )

    def test_schedule_settings(self):
        with patch.dict(os.environ, {"TRADE_INTERVAL": "15m"}, clear=True):
            config = Settings.from_env()
            self.assertEqual(config.candle_close_delay_seconds, 20)
            self.assertEqual(config.position_poll_seconds, 10)


if __name__ == "__main__":
    unittest.main()
