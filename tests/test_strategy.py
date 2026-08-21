import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1] / "app"))

from strategy import classify_trend


def config():
    return SimpleNamespace(
        ema_fast=3, ema_slow=6, atr_period=3, trend_slope_bars=2,
        minimum_trend_separation_atr=0.5,
    )


def candles(values):
    close = np.array(values, dtype=float)
    return pd.DataFrame({
        "open": close - 0.2, "high": close + 1, "low": close - 1, "close": close,
        "volume": np.full(len(close), 10.0),
        "close_time": pd.date_range("2026-01-01", periods=len(close), freq="5min", tz="UTC"),
    })


class TrendTests(unittest.TestCase):
    def test_rising_market_is_uptrend(self):
        self.assertEqual(classify_trend(candles(range(100, 130)), config()), "UP")

    def test_falling_market_is_downtrend(self):
        self.assertEqual(classify_trend(candles(range(130, 100, -1)), config()), "DOWN")

    def test_flat_market_is_range(self):
        self.assertEqual(classify_trend(candles([100] * 30), config()), "RANGE")


if __name__ == "__main__":
    unittest.main()
