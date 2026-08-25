import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1] / "app"))

from llm_decision import _json_object, candle_context, request_decision


def candles():
    close = np.linspace(100, 120, 200)
    open_time = pd.date_range("2026-01-01", periods=200, freq="15min", tz="UTC")
    return pd.DataFrame({
        "open_time": open_time,
        "open": close - .1,
        "high": close + .5,
        "low": close - .5,
        "close": close,
        "volume": np.full(200, 10.0),
        "close_time": open_time + pd.Timedelta(minutes=15) - pd.Timedelta(milliseconds=1),
        "quote_volume": np.full(200, 1000.0),
        "trades": np.full(200, 20),
        "taker_base": np.full(200, 5.0),
        "taker_quote": np.full(200, 500.0),
        "ignore": np.zeros(200),
    })


def config():
    return SimpleNamespace(
        llm_endpoint_url="https://example.invalid", llm_timeout_seconds=10,
        llm_max_entry_deviation_bps=100, symbol="BTCUSDT", interval="15m",
        atr_period=14,
    )


class LlmDecisionTests(unittest.TestCase):
    def test_context_contains_exactly_200_rows_and_ema50(self):
        text = candle_context(candles())
        self.assertEqual(len(text.strip().splitlines()), 201)
        self.assertIn("volume,ema_50", text.splitlines()[0])
        self.assertIn("taker_buy_volume", text.splitlines()[0])

    def test_double_encoded_response_is_supported(self):
        self.assertEqual(_json_object(json.dumps({"direction": "WAIT"}))["direction"], "WAIT")

    @patch("llm_decision.post_json")
    def test_valid_long_response(self, post):
        frame = candles()
        post.return_value = json.dumps({
            "direction": "LONG", "entry_price": 120, "stop_price": 119,
            "target_price": 122, "confidence": 82, "rationale": "HH/HL retest",
            "signal_candle_utc": frame.iloc[-1].close_time.isoformat(),
        })
        decision, analysis = request_decision(frame, config())
        self.assertEqual(decision.action, "LONG")
        self.assertEqual(analysis.invalidation_price, 119)
        self.assertEqual(analysis.target_price, 122)
        sent = post.call_args.args[1]
        self.assertEqual(set(sent), {"system_context", "user_context"})

    @patch("llm_decision.post_json")
    def test_missing_direction_fails_closed(self, post):
        post.return_value = json.dumps({"close": 120})
        with self.assertRaisesRegex(ValueError, "direction"):
            request_decision(candles(), config())

    @patch("llm_decision.post_json")
    def test_incorrect_llm_signal_time_is_ignored(self, post):
        frame = candles()
        post.return_value = {
            "direction": "WAIT", "entry_price": None, "stop_price": None,
            "target_price": None, "confidence": 60, "rationale": "no setup",
            "signal_candle_utc": "2025-04-24T00:05:00Z",
        }
        decision, analysis = request_decision(frame, config())
        self.assertEqual(decision.action, "WAIT")
        self.assertEqual(
            pd.Timestamp(analysis.candle_time), pd.Timestamp(frame.iloc[-1].close_time)
        )

    @patch("llm_decision.post_json")
    def test_sub_two_r_signal_is_rejected(self, post):
        post.return_value = {
            "direction": "SHORT", "entry_price": 120, "stop_price": 121,
            "target_price": 118.5, "confidence": 90, "rationale": "weak payoff",
        }
        with self.assertRaisesRegex(ValueError, "below 2.0"):
            request_decision(candles(), config())


if __name__ == "__main__":
    unittest.main()
