from dataclasses import asdict, dataclass
from enum import Enum

import numpy as np
import pandas as pd


class Signal(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    WAIT = "WAIT"


@dataclass(frozen=True)
class MarketAnalysis:
    strategy_mode: str
    symbol: str
    interval: str
    trend_interval: str
    candle_time: str
    close: float
    rsi: float
    stoch_k: float
    stoch_d: float
    ema_fast: float
    ema_slow: float
    atr: float
    atr_percent: float
    volume_ratio: float
    trend: str
    trend_filter: str
    rule_signal: str
    rule_reason: str

    def to_dict(self):
        return asdict(self)


def rsi(close: pd.Series, period: int) -> pd.Series:
    change = close.diff()
    gain = change.clip(lower=0)
    loss = -change.clip(upper=0)
    average_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    average_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    relative_strength = average_gain / average_loss.replace(0, np.nan)
    values = 100 - (100 / (1 + relative_strength))
    return values.where(average_loss.ne(0), 100).where(average_gain.ne(0), 0)


def stoch_rsi(close: pd.Series, rsi_period: int, stoch_period: int, k: int, d: int):
    rsi_values = rsi(close, rsi_period)
    low = rsi_values.rolling(stoch_period).min()
    high = rsi_values.rolling(stoch_period).max()
    raw = 100 * (rsi_values - low) / (high - low).replace(0, np.nan)
    smooth_k = raw.rolling(k).mean()
    return rsi_values, smooth_k, smooth_k.rolling(d).mean()


def atr(frame: pd.DataFrame, period: int) -> pd.Series:
    previous_close = frame.close.shift(1)
    true_range = pd.concat(
        [
            frame.high - frame.low,
            (frame.high - previous_close).abs(),
            (frame.low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _trend(frame: pd.DataFrame, fast: int, slow: int) -> str:
    fast_value = frame.close.ewm(span=fast, adjust=False).mean().iloc[-1]
    slow_value = frame.close.ewm(span=slow, adjust=False).mean().iloc[-1]
    close = frame.close.iloc[-1]
    if close > slow_value and fast_value > slow_value:
        return "UP"
    if close < slow_value and fast_value < slow_value:
        return "DOWN"
    return "RANGE"


def analyze(candles: pd.DataFrame, trend_candles: pd.DataFrame, config) -> MarketAnalysis:
    required = max(config.ema_slow * 2, config.rsi_period + config.stoch_period + 10)
    if len(candles) < required or len(trend_candles) < config.ema_slow * 2:
        raise ValueError("Not enough closed candles for stable indicators")
    frame = candles.copy()
    frame["ema_fast"] = frame.close.ewm(span=config.ema_fast, adjust=False).mean()
    frame["ema_slow"] = frame.close.ewm(span=config.ema_slow, adjust=False).mean()
    frame["rsi"], frame["k"], frame["d"] = stoch_rsi(
        frame.close, config.rsi_period, config.stoch_period, config.smooth_k, config.smooth_d
    )
    frame["atr"] = atr(frame, config.atr_period)
    frame["volume_average"] = frame.volume.rolling(20).mean()
    previous, current = frame.iloc[-2], frame.iloc[-1]
    needed = [previous.k, previous.d, current.k, current.d, current.atr]
    if any(pd.isna(value) for value in needed):
        raise ValueError("Indicators are not ready")

    trend = _trend(frame, config.ema_fast, config.ema_slow)
    trend_filter = _trend(trend_candles, config.ema_fast, config.ema_slow)
    bullish_cross = previous.k <= previous.d and current.k > current.d
    bearish_cross = previous.k >= previous.d and current.k < current.d
    if bullish_cross and previous.k <= config.oversold and trend == trend_filter == "UP":
        rule_signal, reason = Signal.LONG.value, "oversold cross aligned with execution and filter uptrends"
    elif bearish_cross and previous.k >= config.overbought and trend == trend_filter == "DOWN":
        rule_signal, reason = Signal.SHORT.value, "overbought cross aligned with execution and filter downtrends"
    else:
        rule_signal = Signal.WAIT.value
        reason = (
            "no setup: "
            f"bullish_cross={bullish_cross}, bearish_cross={bearish_cross}, "
            f"previous_k={previous.k:.2f}, current_k={current.k:.2f}, current_d={current.d:.2f}, "
            f"oversold={config.oversold:.2f}, overbought={config.overbought:.2f}, "
            f"trend_{config.interval}={trend}, trend_{config.trend_interval}={trend_filter}"
        )

    return MarketAnalysis(
        strategy_mode=config.strategy_mode,
        symbol=config.symbol,
        interval=config.interval,
        trend_interval=config.trend_interval,
        candle_time=current.close_time.isoformat(),
        close=round(float(current.close), 8),
        rsi=round(float(current.rsi), 4),
        stoch_k=round(float(current.k), 4),
        stoch_d=round(float(current.d), 4),
        ema_fast=round(float(current.ema_fast), 8),
        ema_slow=round(float(current.ema_slow), 8),
        atr=round(float(current.atr), 8),
        atr_percent=round(float(current.atr / current.close), 6),
        volume_ratio=round(float(current.volume / current.volume_average), 4),
        trend=trend,
        trend_filter=trend_filter,
        rule_signal=rule_signal,
        rule_reason=reason,
    )
