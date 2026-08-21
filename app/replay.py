import argparse
import logging
from pathlib import Path

import pandas as pd

from csv_ledger import CsvLedger
from paper_engine import PaperEngine
from settings import Settings
from strategy import Signal, analyze, decide


LOG = logging.getLogger("btc_pullback_replay")


def load_candles(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    lower = {str(column).strip().lower(): column for column in frame.columns}
    required = {"open_time", "open", "high", "low", "close", "volume"}
    if not required.issubset(lower):
        if frame.shape[1] >= 12:
            frame = pd.read_csv(path, header=None)
            frame.columns = [
                "open_time", "open", "high", "low", "close", "volume", "close_time",
                "quote_volume", "trades", "taker_base", "taker_quote", "ignore",
            ]
        else:
            raise ValueError(f"CSV needs columns: {', '.join(sorted(required))}")
    else:
        frame = frame.rename(columns={original: name for name, original in lower.items()})
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    values = pd.to_numeric(frame["open_time"], errors="coerce")
    if values.notna().all():
        unit = "us" if values.iloc[0] > 10**14 else "ms"
        frame["open_time"] = pd.to_datetime(values, unit=unit, utc=True)
    else:
        frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    if "close_time" in frame:
        close_values = pd.to_numeric(frame["close_time"], errors="coerce")
        if close_values.notna().all():
            unit = "us" if close_values.iloc[0] > 10**14 else "ms"
            frame["close_time"] = pd.to_datetime(close_values, unit=unit, utc=True)
        else:
            frame["close_time"] = pd.to_datetime(frame["close_time"], utc=True)
    else:
        frame["close_time"] = frame["open_time"] + pd.Timedelta(minutes=5) - pd.Timedelta(milliseconds=1)
    return frame.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)


def resample_closed(frame: pd.DataFrame, interval: str) -> pd.DataFrame:
    indexed = frame.set_index("open_time")
    result = indexed.resample(interval, label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
        "close_time": "last",
    })
    result = result.dropna().reset_index()
    duration = pd.Timedelta(interval)
    last_close = pd.Timestamp(frame.iloc[-1].close_time)
    return result.loc[result.open_time + duration <= last_close + pd.Timedelta(milliseconds=1)]


def pandas_interval(value: str) -> str:
    number, unit = int(value[:-1]), value[-1]
    return f"{number}{'min' if unit == 'm' else 'h'}"


def run_replay(input_path: Path, output_dir: Path, config: Settings):
    candles = load_candles(input_path)
    trend_all = resample_closed(candles, pandas_interval(config.trend_interval))
    ledger = CsvLedger(output_dir, reset=True)
    replay_config = type(config)(**{**config.__dict__, "data_dir": output_dir})
    state_path = output_dir / "state.json"
    state_path.unlink(missing_ok=True)
    engine = PaperEngine(replay_config, ledger)
    pending = None
    warmup = max(
        config.ema_slow + config.trend_slope_bars,
        config.breakout_lookback + config.pullback_max_bars + 2,
    )

    for index, candle in candles.iterrows():
        now = candle.close_time.to_pydatetime()
        if pending and not engine.position:
            decision, analysis = pending
            engine.open(decision, analysis, float(candle.open), candle.open_time.to_pydatetime())
            pending = None
        if engine.position:
            engine.check_exit_candle(candle, now)
        visible = candles.iloc[max(0, index + 1 - config.candle_limit) : index + 1]
        if len(visible) < warmup:
            continue
        trend = trend_all.loc[trend_all.close_time <= candle.close_time].tail(config.candle_limit)
        try:
            analysis = analyze(visible, trend, config)
        except ValueError:
            continue
        decision = decide(analysis)
        allowed, reason = engine.can_open(decision, analysis, now)
        engine.mark_decision(analysis, decision, result=reason)
        if allowed and decision.action in {Signal.LONG.value, Signal.SHORT.value}:
            pending = (decision, analysis)

    if engine.position:
        last = candles.iloc[-1]
        engine._close_at(float(last.close), "END_OF_DATA", last.close_time.to_pydatetime())
    LOG.info("Replay complete: %d candles, balance %.2f", len(candles), engine.state["balance"])
    LOG.info("Trades: %s", ledger.trades_path)
    LOG.info("Events: %s", ledger.events_path)


def main():
    parser = argparse.ArgumentParser(description="Replay BTCUSDT candles one at a time")
    parser.add_argument("--input", required=True, type=Path, help="Binance kline or OHLCV CSV")
    parser.add_argument("--output", type=Path, default=Path("data/replay"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_replay(args.input, args.output, Settings.from_env())


if __name__ == "__main__":
    main()
