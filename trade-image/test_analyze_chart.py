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
            })

    def test_valid_two_r_long(self):
        MODULE.validate_result({
            "direction": "LONG", "confidence": 80, "entry_price": 100,
            "stop_price": 98, "target_price": 104,
        })

    def test_rejects_sub_two_r_short(self):
        with self.assertRaisesRegex(ValueError, "below 2.0"):
            MODULE.validate_result({
                "direction": "SHORT", "confidence": 80, "entry_price": 100,
                "stop_price": 102, "target_price": 97,
            })


if __name__ == "__main__":
    unittest.main()
