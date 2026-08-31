import importlib.util
import unittest
from pathlib import Path
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch


PATH = Path(__file__).with_name("live_runner.py")
SPEC = importlib.util.spec_from_file_location("binance_live_runner", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BinanceLiveRunnerTests(unittest.TestCase):
    def test_next_close_is_next_quarter_hour(self):
        self.assertEqual(MODULE.next_candle_close_time(901), 1800)

    def test_screenshot_is_ten_seconds_before_close(self):
        self.assertEqual(MODULE.candle_screenshot_time(1800, 10), 1790)

    def test_quantity_rounds_down_to_binance_step(self):
        self.assertAlmostEqual(MODULE.round_down(0.0059, 0.001), 0.005)

    def test_long_exit_and_conservative_stop_first(self):
        trade = {"side": "LONG", "stop": 90, "target": 120}
        self.assertEqual(MODULE.exit_for_candle(trade, {"high": 121, "low": 100}), (120, "TARGET"))
        self.assertEqual(MODULE.exit_for_candle(trade, {"high": 121, "low": 89}), (90, "STOP"))

    def test_short_exit(self):
        trade = {"side": "SHORT", "stop": 110, "target": 80}
        self.assertEqual(MODULE.exit_for_candle(trade, {"high": 105, "low": 79}), (80, "TARGET"))

    def test_adverse_slippage(self):
        self.assertAlmostEqual(MODULE.adverse_fill(100, "LONG", True, 1), 100.01)
        self.assertAlmostEqual(MODULE.adverse_fill(100, "LONG", False, 1), 99.99)

    def test_real_quantity_never_rounds_above_notional(self):
        self.assertEqual(MODULE.decimal_floor(0.00128, Decimal("0.001")), Decimal("0.001"))

    def test_weighted_real_fills_include_actual_commission_and_pnl(self):
        fills = [
            {"price": "78000", "qty": "0.001", "commission": "0.039", "commissionAsset": "USDT",
             "realizedPnl": "0"},
            {"price": "78200", "qty": "0.001", "commission": "0.0391", "commissionAsset": "USDT",
             "realizedPnl": "0.2"},
        ]
        price, qty, fee, pnl = MODULE.weighted_fill(fills)
        self.assertEqual((price, qty), (78100.0, 0.002))
        self.assertAlmostEqual(fee, 0.0781)
        self.assertAlmostEqual(pnl, 0.2)

    def test_empty_position_response_means_flat(self):
        client = object.__new__(MODULE.BinanceFuturesClient)
        client.signed = lambda *_args, **_kwargs: []
        self.assertEqual(client.position_amount(), Decimal("0"))

    def test_live_chart_analysis_uses_gemini(self):
        args = SimpleNamespace(model="gemini-test", llm_timeout=30, llm_attempts=1, retry_delay=1)
        expected = ({"direction": "WAIT"}, {"raw": True})
        with patch.object(MODULE, "analyze", return_value=expected) as analyze:
            result = MODULE.analyze_with_retry(Path("chart.png"), Path("context.txt"), args, None)
        self.assertEqual(result, expected)
        self.assertEqual(analyze.call_args.kwargs["provider"], "gemini")

    def test_gemini_context_has_strict_identifiable_setups(self):
        context = (PATH.parent / "gemini_system_context.txt").read_text(encoding="utf-8")
        self.assertIn("ALLOWED SETUP 1 — TREND PULLBACK CONTINUATION", context)
        self.assertIn("ALLOWED SETUP 2 — BREAKOUT AND RETEST", context)
        self.assertIn("ALLOWED SETUP 3 — RANGE-EDGE REJECTION", context)
        self.assertIn("ALLOWED SETUP 4 — CONFIRMED STRUCTURE REVERSAL", context)
        self.assertIn("INVALIDATION — STRUCTURAL STOP WITH NOISE BUFFER", context)
        self.assertIn("Do not claim a target is 2R without showing correct arithmetic", context)


if __name__ == "__main__":
    unittest.main()
