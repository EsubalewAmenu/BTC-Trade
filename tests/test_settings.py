import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "app"))

from settings import Settings, _interval_seconds


class SettingsTests(unittest.TestCase):
    def test_interval_seconds(self):
        self.assertEqual(_interval_seconds("15m"), 900)
        self.assertEqual(_interval_seconds("1h"), 3600)

    def test_polling_is_one_third_of_trade_interval(self):
        with patch.dict(os.environ, {"TRADE_INTERVAL": "15m"}, clear=True):
            self.assertEqual(Settings.from_env().poll_seconds, 300)
        with patch.dict(os.environ, {"TRADE_INTERVAL": "1h"}, clear=True):
            self.assertEqual(Settings.from_env().poll_seconds, 1200)

    def test_poll_seconds_environment_value_does_not_override_schedule(self):
        with patch.dict(
            os.environ, {"TRADE_INTERVAL": "15m", "POLL_SECONDS": "10"}, clear=True
        ):
            self.assertEqual(Settings.from_env().poll_seconds, 300)


if __name__ == "__main__":
    unittest.main()
