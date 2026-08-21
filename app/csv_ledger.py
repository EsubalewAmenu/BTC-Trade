import csv
from pathlib import Path


EVENT_FIELDS = [
    "candle_time", "price", "signal", "result", "trend", "higher_trend", "context_trend",
    "impulse", "pullback", "confirmation", "pullback_bars", "pullback_depth", "breakout_level",
    "invalidation_price", "reason",
]
TRADE_FIELDS = [
    "trade_id", "status", "side", "opened_at", "closed_at", "entry_price",
    "exit_price", "quantity_btc", "notional_usdt", "stop_price", "target_price",
    "entry_fee", "exit_fee", "gross_pnl", "net_pnl", "exit_reason",
    "hold_minutes", "signal_candle", "balance_after", "rationale",
]


class CsvLedger:
    def __init__(self, data_dir: Path, reset: bool = False):
        directory = Path(data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self.events_path = directory / "events.csv"
        self.trades_path = directory / "trades.csv"
        if reset:
            self.events_path.unlink(missing_ok=True)
            self.trades_path.unlink(missing_ok=True)
        self._ensure(self.events_path, EVENT_FIELDS)
        self._ensure(self.trades_path, TRADE_FIELDS)

    @staticmethod
    def _ensure(path, fields):
        if not path.exists():
            with path.open("w", newline="", encoding="utf-8") as handle:
                csv.DictWriter(handle, fieldnames=fields).writeheader()

    @staticmethod
    def _append(path, fields, row):
        with path.open("a", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=fields).writerow(row)

    def record_event(self, analysis, result):
        self._append(self.events_path, EVENT_FIELDS, {
            "candle_time": analysis.candle_time,
            "price": analysis.close,
            "signal": analysis.rule_signal,
            "result": result,
            "trend": analysis.trend,
            "higher_trend": analysis.trend_filter,
            "context_trend": analysis.context_trend,
            "impulse": analysis.impulse_found,
            "pullback": analysis.pullback_found,
            "confirmation": analysis.confirmation_found,
            "pullback_bars": analysis.pullback_bars,
            "pullback_depth": analysis.pullback_depth,
            "breakout_level": analysis.breakout_level,
            "invalidation_price": analysis.invalidation_price,
            "reason": analysis.rule_reason,
        })

    def open_trade(self, position, analysis):
        self._append(self.trades_path, TRADE_FIELDS, {
            "trade_id": position.trade_id, "status": "OPEN", "side": position.side,
            "opened_at": position.opened_at, "closed_at": "",
            "entry_price": position.entry_price, "exit_price": "",
            "quantity_btc": position.quantity, "notional_usdt": position.notional,
            "stop_price": position.stop_price, "target_price": position.target_price,
            "entry_fee": position.entry_fee, "exit_fee": "", "gross_pnl": "",
            "net_pnl": "", "exit_reason": "", "hold_minutes": "",
            "signal_candle": position.candle_time, "balance_after": "",
            "rationale": position.rationale,
        })

    def close_trade(self, trade_id, result):
        with self.trades_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            if row["trade_id"] == trade_id and row["status"] == "OPEN":
                row.update({
                    "status": "CLOSED", "closed_at": result["closed_at"],
                    "exit_price": result["exit_price"], "exit_fee": result["exit_fee"],
                    "gross_pnl": result["gross_pnl"], "net_pnl": result["net_pnl"],
                    "exit_reason": result["exit_reason"],
                    "hold_minutes": result["hold_minutes"],
                    "balance_after": result["balance"],
                })
                break
        temporary = self.trades_path.with_suffix(".tmp")
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=TRADE_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(self.trades_path)
