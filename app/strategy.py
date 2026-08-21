from dataclasses import asdict, dataclass
from enum import Enum

import pandas as pd


class Signal(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    WAIT = "WAIT"


@dataclass(frozen=True)
class Decision:
    action: str
    rationale: str


@dataclass(frozen=True)
class MarketAnalysis:
    symbol: str
    interval: str
    trend_interval: str
    candle_time: str
    close: float
    atr: float
    ema_fast: float
    ema_slow: float
    trend: str
    trend_filter: str
    impulse_found: bool
    pullback_found: bool
    confirmation_found: bool
    rule_signal: str
    rule_reason: str

    def to_dict(self):
        return asdict(self)


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


def add_indicators(frame: pd.DataFrame, config) -> pd.DataFrame:
    result = frame.copy()
    result["ema_fast"] = result.close.ewm(span=config.ema_fast, adjust=False).mean()
    result["ema_slow"] = result.close.ewm(span=config.ema_slow, adjust=False).mean()
    result["atr"] = atr(result, config.atr_period)
    return result


def classify_trend(frame: pd.DataFrame, config) -> str:
    """Classify only from closed candles; EMA slope prevents a flat crossover signal."""
    if len(frame) < config.ema_slow + config.trend_slope_bars:
        return "RANGE"
    enriched = add_indicators(frame, config)
    current = enriched.iloc[-1]
    previous = enriched.iloc[-1 - config.trend_slope_bars]
    if (
        current.close > current.ema_fast > current.ema_slow
        and current.ema_fast > previous.ema_fast
        and current.ema_slow > previous.ema_slow
    ):
        return "UP"
    if (
        current.close < current.ema_fast < current.ema_slow
        and current.ema_fast < previous.ema_fast
        and current.ema_slow < previous.ema_slow
    ):
        return "DOWN"
    return "RANGE"


def analyze(candles: pd.DataFrame, trend_candles: pd.DataFrame, config) -> MarketAnalysis:
    required = config.ema_slow + config.impulse_lookback + config.pullback_max_bars + 2
    if len(candles) < required or len(trend_candles) < config.ema_slow + config.trend_slope_bars:
        raise ValueError("Not enough closed candles for pullback analysis")

    frame = add_indicators(candles, config)
    current = frame.iloc[-1]
    if pd.isna(current.atr) or current.atr <= 0:
        raise ValueError("ATR is not ready")

    trend = classify_trend(candles, config)
    trend_filter = classify_trend(trend_candles, config)
    pullback = frame.iloc[-1 - config.pullback_max_bars : -1]
    impulse = frame.iloc[
        -1 - config.pullback_max_bars - config.impulse_lookback : -1 - config.pullback_max_bars
    ]
    recent = pullback.iloc[-config.pullback_min_bars :]
    tolerance = config.pullback_touch_atr * current.atr

    long_impulse = bool(
        ((impulse.high - impulse.ema_fast) >= config.impulse_atr * impulse.atr).any()
    )
    short_impulse = bool(
        ((impulse.ema_fast - impulse.low) >= config.impulse_atr * impulse.atr).any()
    )
    long_pullback = bool(
        (recent.close < recent.open).any()
        and (pullback.low <= pullback.ema_fast + tolerance).any()
        and (pullback.close > pullback.ema_slow).all()
    )
    short_pullback = bool(
        (recent.close > recent.open).any()
        and (pullback.high >= pullback.ema_fast - tolerance).any()
        and (pullback.close < pullback.ema_slow).all()
    )
    long_confirmation = bool(
        current.close > current.open
        and current.close > frame.iloc[-2].high
        and current.close > current.ema_fast
    )
    short_confirmation = bool(
        current.close < current.open
        and current.close < frame.iloc[-2].low
        and current.close < current.ema_fast
    )

    if trend == trend_filter == "UP" and long_impulse and long_pullback and long_confirmation:
        signal = Signal.LONG.value
        reason = "bullish trends, prior impulse, controlled EMA pullback, bullish confirmation"
        impulse_found, pullback_found, confirmation_found = True, True, True
    elif trend == trend_filter == "DOWN" and short_impulse and short_pullback and short_confirmation:
        signal = Signal.SHORT.value
        reason = "bearish trends, prior impulse, controlled EMA pullback, bearish confirmation"
        impulse_found, pullback_found, confirmation_found = True, True, True
    else:
        signal = Signal.WAIT.value
        if trend != trend_filter or trend == "RANGE":
            reason = f"trend not aligned: execution={trend}, higher={trend_filter}"
        elif trend == "UP":
            impulse_found, pullback_found, confirmation_found = (
                long_impulse, long_pullback, long_confirmation
            )
            reason = (
                f"bullish context incomplete: impulse={long_impulse}, "
                f"pullback={long_pullback}, confirmation={long_confirmation}"
            )
        else:
            impulse_found, pullback_found, confirmation_found = (
                short_impulse, short_pullback, short_confirmation
            )
            reason = (
                f"bearish context incomplete: impulse={short_impulse}, "
                f"pullback={short_pullback}, confirmation={short_confirmation}"
            )
        if trend != trend_filter or trend == "RANGE":
            impulse_found = pullback_found = confirmation_found = False

    return MarketAnalysis(
        symbol=config.symbol,
        interval=config.interval,
        trend_interval=config.trend_interval,
        candle_time=pd.Timestamp(current.close_time).isoformat(),
        close=round(float(current.close), 8),
        atr=round(float(current.atr), 8),
        ema_fast=round(float(current.ema_fast), 8),
        ema_slow=round(float(current.ema_slow), 8),
        trend=trend,
        trend_filter=trend_filter,
        impulse_found=impulse_found,
        pullback_found=pullback_found,
        confirmation_found=confirmation_found,
        rule_signal=signal,
        rule_reason=reason,
    )


def decide(analysis: MarketAnalysis) -> Decision:
    return Decision(analysis.rule_signal, analysis.rule_reason)
