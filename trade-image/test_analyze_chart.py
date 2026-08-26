import importlib.util
import json
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("analyze_chart.py")
SPEC = importlib.util.spec_from_file_location("trade_image_analyze", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ChartAnalysisTests(unittest.TestCase):
    def test_extracts_candidate_json(self):
        payload = {"candidates": [{"content": {"parts": [{"text": '{"direction":"WAIT"}'}]}}]}
        self.assertEqual(json.loads(MODULE.response_text(payload))["direction"], "WAIT")

    def test_wait_requires_null_prices(self):
        with self.assertRaisesRegex(ValueError, "WAIT must use null"):
            MODULE.validate_result({
                "direction": "WAIT", "confidence": 50, "entry_price": 100,
                "stop_price": None, "target_price": None,
                "candle_high": 105, "candle_low": 95, "candle_close": 100,
            })

    def test_valid_two_r_long(self):
        MODULE.validate_result({
            "direction": "LONG", "confidence": 80, "entry_price": 100,
            "stop_price": 98, "target_price": 104,
            "candle_high": 101, "candle_low": 99, "candle_close": 100,
        })

    def test_rejects_sub_two_r_short(self):
        with self.assertRaisesRegex(ValueError, "below 2.0"):
            MODULE.validate_result({
                "direction": "SHORT", "confidence": 80, "entry_price": 100,
                "stop_price": 102, "target_price": 97,
                "candle_high": 101, "candle_low": 99, "candle_close": 100,
            })

    def test_accepts_tiny_chart_rounding_difference_at_two_r(self):
        MODULE.validate_result({
            "direction": "LONG", "confidence": 80, "entry_price": 100,
            "stop_price": 90, "target_price": 119.995,
            "candle_high": 101, "candle_low": 99, "candle_close": 100,
        })

    def test_rejects_retroactive_entry_as_wait(self):
        result = MODULE.reject_unexecutable_signal({
            "direction": "LONG", "entry_price": 64668.57, "stop_price": 62500,
            "target_price": 72689.38, "confidence": 95, "rationale": "bad OCR",
            "candle_high": 72689.38, "candle_low": 69716.72,
            "candle_close": 72689.38, "candle_utc": "04:38 UTC",
        })
        self.assertEqual(result["direction"], "WAIT")
        self.assertIsNone(result["entry_price"])
        self.assertIn("unexecutable", result["rationale"])

    def test_keeps_executable_current_price_signal(self):
        result = {
            "direction": "LONG", "entry_price": 100, "stop_price": 98,
            "target_price": 104, "confidence": 80, "rationale": "pullback",
            "candle_high": 101, "candle_low": 99, "candle_close": 100,
            "candle_utc": "now",
        }
        self.assertEqual(MODULE.reject_unexecutable_signal(result)["direction"], "LONG")

    def test_sub_two_r_model_signal_becomes_wait(self):
        result = MODULE.validate_and_normalize_result({
            "direction": "LONG", "confidence": 80, "entry_price": 100,
            "stop_price": 90, "target_price": 114.8, "rationale": "weak plan",
            "candle_high": 101, "candle_low": 99, "candle_close": 100,
            "candle_utc": "now",
        })
        self.assertEqual(result["direction"], "WAIT")
        self.assertIn("1.480000", result["rationale"])

    def test_invalid_candle_data_remains_fatal(self):
        with self.assertRaisesRegex(ValueError, "invalid current candle"):
            MODULE.validate_and_normalize_result({
                "direction": "LONG", "confidence": 80, "entry_price": 100,
                "stop_price": 90, "target_price": 120, "rationale": "bad candle",
                "candle_high": 90, "candle_low": 110, "candle_close": 100,
                "candle_utc": "now",
            })


if __name__ == "__main__":
    unittest.main()
