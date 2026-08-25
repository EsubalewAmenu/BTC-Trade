import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from replay_runner import (
    FORWARD_SELECTORS,
    append_signal_csv,
    initialize_trade_csv,
    cached_forward_ready,
    exit_for_candle,
    is_forward_ready,
    is_retryable_gemini_error,
    gemini_retry_delay,
    signal_row,
)


class FakeElement:
    def __init__(self, classes="", aria_disabled="false", displayed=True, enabled=True):
        self.values = {"class": classes, "aria-disabled": aria_disabled}
        self.displayed = displayed
        self.enabled = enabled

    def get_attribute(self, name):
        return self.values.get(name)

    def is_displayed(self):
        return self.displayed

    def is_enabled(self):
        return self.enabled


class StaleElement(FakeElement):
    def get_attribute(self, name):
        raise RuntimeError("stale")


class ReplayRunnerTests(unittest.TestCase):
    def test_supports_tradingview_dynamic_tooltip(self):
        self.assertIn("[data-tooltip*='Forward']", FORWARD_SELECTORS)

    def test_enabled_forward_is_ready(self):
        self.assertTrue(is_forward_ready(FakeElement("controls__button button")))

    def test_tradingview_disabled_class_is_not_ready(self):
        self.assertFalse(is_forward_ready(FakeElement("button isDisabled")))

    def test_aria_disabled_is_not_ready(self):
        self.assertFalse(is_forward_ready(FakeElement(aria_disabled="true")))

    def test_cached_forward_survives_missing_title(self):
        self.assertTrue(cached_forward_ready(FakeElement(classes="button")))

    def test_stale_cached_forward_requests_rediscovery(self):
        self.assertFalse(cached_forward_ready(StaleElement()))

    def test_503_is_retryable_but_validation_is_not(self):
        self.assertTrue(is_retryable_gemini_error(RuntimeError("Gemini HTTP 503: busy")))
        self.assertFalse(is_retryable_gemini_error(ValueError("invalid JSON")))

    def test_uses_gemini_retry_delay_with_safety_margin(self):
        error = RuntimeError('"retryDelay": "19s" and Please retry in 19.321s')
        self.assertAlmostEqual(gemini_retry_delay(error, 10), 20.321)

    def test_signal_csv_has_header_and_signal(self):
        decision = {
            "direction": "LONG", "entry_price": 100, "stop_price": 90,
            "target_price": 120, "confidence": 80, "rationale": "pullback",
        }
        row = signal_row("run", 2, "2026-01-01T00:00:00+00:00", Path("chart.png"), decision)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "signals.csv"
            append_signal_csv(path, row)
            content = path.read_text(encoding="utf-8")
        self.assertIn("Trade ID,Status,Side", content)
        self.assertIn("run-00002,SIGNAL,LONG", content)

    def test_long_target_and_short_stop(self):
        self.assertEqual(
            exit_for_candle({"side": "LONG", "stop": 90, "target": 120}, 121, 100),
            (120, "TARGET"),
        )
        self.assertEqual(
            exit_for_candle({"side": "SHORT", "stop": 110, "target": 80}, 111, 90),
            (110, "STOP"),
        )

    def test_both_levels_hit_uses_conservative_stop(self):
        self.assertEqual(
            exit_for_candle({"side": "LONG", "stop": 90, "target": 120}, 125, 85),
            (90, "STOP"),
        )

    def test_trade_csv_exists_before_first_trade_closes(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "trades.csv"
            initialize_trade_csv(path)
            content = path.read_text(encoding="utf-8")
        self.assertIn("Gross PnL,Net PnL,R Multiple,Exit Reason", content)


if __name__ == "__main__":
    unittest.main()
