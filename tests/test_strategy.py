import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1] / "app"))

from strategy import Signal, analyze, rsi


def config():
    return SimpleNamespace(
        strategy_mode="scalp", symbol="BTCUSDT", interval="5m", trend_interval="15m",
        ema_fast=2, ema_slow=3,
        rsi_period=2, stoch_period=2, smooth_k=1, smooth_d=1,
        oversold=20, overbought=80, atr_period=2,
    )


def candles(values):
    count = len(values)
    close = np.array(values, dtype=float)
    return pd.DataFrame({
        "high": close + 1, "low": close - 1, "close": close,
        "volume": np.full(count, 10.0),
        "close_time": pd.date_range("2026-01-01", periods=count, freq="15min", tz="UTC"),
    })


class StrategyTests(unittest.TestCase):
    def test_rsi_reaches_extremes(self):
        self.assertEqual(rsi(pd.Series(range(20)), 5).iloc[-1], 100)
        self.assertEqual(rsi(pd.Series(range(20, 0, -1)), 5).iloc[-1], 0)

    @patch("strategy.stoch_rsi")
    def test_long_requires_cross_and_two_uptrends(self, indicator):
        k, d = np.full(20, 10.0), np.full(20, 15.0)
        k[-1], d[-1] = 25.0, 20.0
        indicator.return_value = pd.Series(np.full(20, 40.0)), pd.Series(k), pd.Series(d)
        result = analyze(candles(range(100, 120)), candles(range(100, 120)), config())
        self.assertEqual(result.rule_signal, Signal.LONG.value)

    @patch("strategy.stoch_rsi")
    def test_short_requires_cross_and_two_downtrends(self, indicator):
        k, d = np.full(20, 90.0), np.full(20, 85.0)
        k[-1], d[-1] = 75.0, 80.0
        indicator.return_value = pd.Series(np.full(20, 60.0)), pd.Series(k), pd.Series(d)
        result = analyze(candles(range(120, 100, -1)), candles(range(120, 100, -1)), config())
        self.assertEqual(result.rule_signal, Signal.SHORT.value)


if __name__ == "__main__":
    unittest.main()
