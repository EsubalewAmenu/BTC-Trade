import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "app"))

from llm_decider import Decision
from paper_engine import PaperEngine


class Ledger:
    def __init__(self): self.closed = None
    def record_event(self, *args): pass
    def open_trade(self, *args): pass
    def close_trade(self, trade_id, result): self.closed = result


def config(path):
    return SimpleNamespace(
        data_dir=Path(path), paper_start_balance=500, max_trades_per_day=4,
        max_daily_loss=.02, min_llm_confidence=.7, cooldown_minutes=0,
        slippage_bps=0, stop_atr=1, risk_per_trade=.005, max_leverage=3,
        reward_risk=2, taker_fee_rate=.0005, max_hold_minutes=240,
    )


def analysis():
    return SimpleNamespace(candle_time="2026-01-01T00:15:00+00:00", trend_1h="UP", atr=100.0)


class PaperEngineTests(unittest.TestCase):
    def test_long_target_closes_and_increases_balance(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger()
            engine = PaperEngine(config(directory), ledger)
            decision = Decision("LONG", .9, "test")
            allowed, _ = engine.can_open(decision, analysis(), datetime(2026, 1, 1, tzinfo=timezone.utc))
            self.assertTrue(allowed)
            position = engine.open(decision, analysis(), 100_000, datetime(2026, 1, 1, tzinfo=timezone.utc))
            result = engine.check_exit(position.target_price, datetime(2026, 1, 1, 1, tzinfo=timezone.utc))
            self.assertEqual(result["exit_reason"], "TARGET")
            self.assertGreater(result["net_pnl"], 0)
            self.assertIsNone(engine.position)

    def test_low_confidence_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = PaperEngine(config(directory), Ledger())
            allowed, reason = engine.can_open(Decision("LONG", .5, "weak"), analysis())
            self.assertFalse(allowed)
            self.assertIn("confidence", reason)


if __name__ == "__main__":
    unittest.main()
