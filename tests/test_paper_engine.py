import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1] / "app"))

from paper_engine import PaperEngine
from strategy import Decision


class Ledger:
    def __init__(self): self.closed = None
    def record_event(self, *args): pass
    def open_trade(self, *args): pass
    def close_trade(self, trade_id, result): self.closed = result


def config(path):
    return SimpleNamespace(
        data_dir=Path(path), paper_start_balance=500, max_trades_per_day=4,
        max_daily_loss=.02, cooldown_minutes=0, slippage_bps=0, stop_atr=1,
        risk_per_trade=.005, max_leverage=3, reward_risk=2,
        taker_fee_rate=.0005, max_hold_minutes=240,
    )


def analysis():
    return SimpleNamespace(
        candle_time="2026-01-01T00:15:00+00:00", trend_filter="UP", atr=100.0
    )


class PaperEngineTests(unittest.TestCase):
    def test_long_target_closes_and_increases_balance(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger()
            engine = PaperEngine(config(directory), ledger)
            decision = Decision("LONG", "valid pullback")
            now = datetime(2026, 1, 1, tzinfo=timezone.utc)
            allowed, _ = engine.can_open(decision, analysis(), now)
            self.assertTrue(allowed)
            position = engine.open(decision, analysis(), 100_000, now)
            candle = SimpleNamespace(
                low=position.entry_price, high=position.target_price + 1,
                close=position.target_price, close_time=pd.Timestamp("2026-01-01T01:00:00Z"),
            )
            result = engine.check_exit_candle(candle)
            self.assertEqual(result["exit_reason"], "TARGET")
            self.assertGreater(result["net_pnl"], 0)

    def test_same_candle_stop_and_target_uses_conservative_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = PaperEngine(config(directory), Ledger())
            now = datetime(2026, 1, 1, tzinfo=timezone.utc)
            position = engine.open(Decision("LONG", "test"), analysis(), 100_000, now)
            candle = SimpleNamespace(
                low=position.stop_price - 1, high=position.target_price + 1,
                close=position.entry_price, close_time=pd.Timestamp("2026-01-01T00:05:00Z"),
            )
            self.assertEqual(engine.check_exit_candle(candle)["exit_reason"], "STOP")


if __name__ == "__main__":
    unittest.main()
