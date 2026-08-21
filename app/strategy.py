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
    pullback_bars: int
    breakout_level: float | None
    invalidation_price: float | None
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
    required = max(
        config.ema_slow + config.trend_slope_bars,
        config.breakout_lookback + config.pullback_max_bars + 2,
    )
    if len(candles) < required or len(trend_candles) < config.ema_slow + config.trend_slope_bars:
        raise ValueError("Not enough closed candles for pullback analysis")

    frame = add_indicators(candles, config)
    current = frame.iloc[-1]
    if pd.isna(current.atr) or current.atr <= 0:
        raise ValueError("ATR is not ready")

    trend = classify_trend(candles, config)
    trend_filter = classify_trend(trend_candles, config)
    tolerance = config.pullback_touch_atr * current.atr
    confirmation_window = frame.iloc[-1 - config.confirmation_lookback : -1]
    long_confirmation = bool(
        current.close > current.open
        and current.close > confirmation_window.high.max()
        and current.close > current.ema_fast
    )
    short_confirmation = bool(
        current.close < current.open
        and current.close < confirmation_window.low.min()
        and current.close < current.ema_fast
    )

    # A candidate impulse must actually break prior structure. The candles between that
    # breakout and the current confirmation are the pullback, so its length is genuinely
    # variable rather than always being PULLBACK_MAX_BARS.
    long_setup = short_setup = None
    long_impulse = short_impulse = False
    long_pullback = short_pullback = False
    end = len(frame) - 1
    for pullback_bars in range(config.pullback_min_bars, config.pullback_max_bars + 1):
        impulse_index = end - pullback_bars - 1
        prior_start = impulse_index - config.breakout_lookback
        if prior_start < 0:
            continue
        impulse = frame.iloc[impulse_index]
        prior = frame.iloc[prior_start:impulse_index]
        pullback = frame.iloc[impulse_index + 1:end]
        impulse_range = float(impulse.high - impulse.low)
        average_pullback_range = float((pullback.high - pullback.low).mean())
        controlled = average_pullback_range <= impulse_range * config.pullback_max_retrace

        long_breakout = bool(
            impulse.close > prior.high.max()
            and impulse.close > impulse.open
            and impulse.close - impulse.ema_fast >= config.impulse_atr * impulse.atr
        )
        short_breakout = bool(
            impulse.close < prior.low.min()
            and impulse.close < impulse.open
            and impulse.ema_fast - impulse.close >= config.impulse_atr * impulse.atr
        )
        long_impulse = long_impulse or long_breakout
        short_impulse = short_impulse or short_breakout
        breakout_high = float(prior.high.max())
        breakout_low = float(prior.low.min())
        candidate_long_pullback = bool(
            long_breakout
            and controlled
            and (pullback.close < pullback.open).any()
            and ((pullback.low <= pullback.ema_fast + tolerance).any()
                 or (pullback.low <= breakout_high + tolerance).any())
            and (pullback.close >= breakout_high - tolerance).all()
            and (pullback.close > pullback.ema_slow).all()
        )
        candidate_short_pullback = bool(
            short_breakout
            and controlled
            and (pullback.close > pullback.open).any()
            and ((pullback.high >= pullback.ema_fast - tolerance).any()
                 or (pullback.high >= breakout_low - tolerance).any())
            and (pullback.close <= breakout_low + tolerance).all()
            and (pullback.close < pullback.ema_slow).all()
        )
        long_pullback = long_pullback or candidate_long_pullback
        short_pullback = short_pullback or candidate_short_pullback
        if candidate_long_pullback and long_confirmation and long_setup is None:
            long_setup = (pullback_bars, breakout_high, float(pullback.low.min()))
        if candidate_short_pullback and short_confirmation and short_setup is None:
            short_setup = (pullback_bars, breakout_low, float(pullback.high.max()))

    pullback_bars = 0
    breakout_level = invalidation_price = None
    if trend == trend_filter == "UP" and long_setup:
        signal = Signal.LONG.value
        pullback_bars, breakout_level, pullback_low = long_setup
        invalidation_price = pullback_low - config.stop_buffer_atr * current.atr
        reason = "bullish trends, breakout impulse, controlled structure retest, bullish confirmation"
        impulse_found, pullback_found, confirmation_found = True, True, True
    elif trend == trend_filter == "DOWN" and short_setup:
        signal = Signal.SHORT.value
        pullback_bars, breakout_level, pullback_high = short_setup
        invalidation_price = pullback_high + config.stop_buffer_atr * current.atr
        reason = "bearish trends, breakout impulse, controlled structure retest, bearish confirmation"
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
        pullback_bars=pullback_bars,
        breakout_level=None if breakout_level is None else round(breakout_level, 8),
        invalidation_price=None if invalidation_price is None else round(invalidation_price, 8),
        rule_signal=signal,
        rule_reason=reason,
    )


def decide(analysis: MarketAnalysis) -> Decision:
    return Decision(analysis.rule_signal, analysis.rule_reason)
