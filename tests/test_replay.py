import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1] / "app"))

from replay import resample_closed


class ReplayTests(unittest.TestCase):
    def test_resample_excludes_incomplete_higher_timeframe_candle(self):
        opens = pd.date_range("2026-01-01", periods=5, freq="5min", tz="UTC")
        frame = pd.DataFrame({
            "open_time": opens,
            "open": [100, 101, 102, 103, 104],
            "high": [101, 102, 103, 104, 105],
            "low": [99, 100, 101, 102, 103],
            "close": [101, 102, 103, 104, 105],
            "volume": [1] * 5,
            "close_time": opens + pd.Timedelta(minutes=5) - pd.Timedelta(milliseconds=1),
        })
        result = resample_closed(frame, "15min")
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0].close, 103)


if __name__ == "__main__":
    unittest.main()
