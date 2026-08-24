import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _int(name: str, default: int, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _float(name: str, default: float, minimum: float, maximum: float) -> float:
    value = float(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _interval_seconds(interval: str) -> int:
    value = interval.strip().lower()
    if len(value) < 2 or not value[:-1].isdigit() or value[-1] not in {"m", "h"}:
        raise ValueError("TRADE_INTERVAL must use minutes or hours, for example 15m or 1h")
    multiplier = 60 if value[-1] == "m" else 3600
    seconds = int(value[:-1]) * multiplier
    if seconds <= 0:
        raise ValueError("TRADE_INTERVAL must be positive")
    return seconds


def seconds_until_candle_request(
    last_candle_time: str | None, interval: str, close_delay_seconds: int,
    now: datetime | None = None,
) -> float:
    if not last_candle_time:
        return 0.0
    current = now or datetime.now(timezone.utc)
    last_close = datetime.fromisoformat(last_candle_time)
    if last_close.tzinfo is None:
        last_close = last_close.replace(tzinfo=timezone.utc)
    request_at = last_close + timedelta(
        seconds=_interval_seconds(interval) + close_delay_seconds
    )
    return max(0.0, (request_at - current).total_seconds())


@dataclass(frozen=True)
class Settings:
    decision_mode: str
    log_external_responses: bool
    llm_endpoint_url: str
    llm_timeout_seconds: int
    llm_min_confidence: float
    llm_max_entry_deviation_bps: float
    strategy_variant: str
    symbol: str
    interval: str
    trend_interval: str
    context_interval: str
    candle_limit: int
    candle_close_delay_seconds: int
    position_poll_seconds: int
    max_consecutive_errors: int
    ema_fast: int
    ema_slow: int
    atr_period: int
    trend_slope_bars: int
    minimum_trend_separation_atr: float
    breakout_lookback: int
    impulse_atr: float
    pullback_min_bars: int
    pullback_max_bars: int
    pullback_touch_atr: float
    pullback_max_retrace: float
    pullback_min_depth: float
    pullback_max_depth: float
    confirmation_lookback: int
    confirmation_close_fraction: float
    stop_atr: float
    stop_buffer_atr: float
    reward_risk: float
    minimum_net_reward_risk: float
    breakeven_trigger_r: float
    risk_per_trade: float
    max_daily_loss: float
    max_trades_per_day: int
    max_leverage: float
    paper_start_balance: float
    taker_fee_rate: float
    slippage_bps: float
    max_hold_minutes: int
    cooldown_minutes: int
    binance_base_url: str
    binance_api_key: str
    binance_secret_key: str
    require_binance_account: bool
    request_timeout: int
    data_dir: Path
    telegram_token: str
    telegram_chat_id: str
    log_level: str

    @classmethod
    def from_env(cls):
        decision_mode = os.getenv("DECISION_MODE", "llm").strip().lower()
        if decision_mode not in {"deterministic", "llm"}:
            raise ValueError("DECISION_MODE must be deterministic or llm")
        variant = os.getenv("STRATEGY_VARIANT", "breakout_retest").strip().lower()
        if variant not in {"breakout_retest", "ema_pullback"}:
            raise ValueError("STRATEGY_VARIANT must be breakout_retest or ema_pullback")
        fast = _int("EMA_FAST", 20, 2)
        slow = _int("EMA_SLOW", 50, 3)
        if fast >= slow:
            raise ValueError("EMA_FAST must be smaller than EMA_SLOW")
        minimum = _int("PULLBACK_MIN_BARS", 2, 1)
        maximum = _int("PULLBACK_MAX_BARS", 6, 2)
        if minimum > maximum:
            raise ValueError("PULLBACK_MIN_BARS must not exceed PULLBACK_MAX_BARS")
        symbol = os.getenv("TRADE_SYMBOL", "BTCUSDT").upper()
        if symbol != "BTCUSDT":
            raise ValueError("This trader intentionally supports BTCUSDT only")
        level = os.getenv("LOG_LEVEL", "INFO").upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("LOG_LEVEL is invalid")
        interval = os.getenv("TRADE_INTERVAL", "15m").strip().lower()
        return cls(
            decision_mode=decision_mode,
            log_external_responses=_bool("LOG_EXTERNAL_RESPONSES", True),
            llm_endpoint_url=os.getenv(
                "LLM_ENDPOINT_URL",
                "https://63aghx6yiwuo5m3hwlxxsywwb40lwpyg.lambda-url.us-east-1.on.aws/",
            ).strip(),
            llm_timeout_seconds=_int("LLM_TIMEOUT_SECONDS", 60, 1),
            llm_min_confidence=_float("LLM_MIN_CONFIDENCE", 70, 0, 100),
            llm_max_entry_deviation_bps=_float(
                "LLM_MAX_ENTRY_DEVIATION_BPS", 20, 0, 1000
            ),
            strategy_variant=variant,
            symbol=symbol,
            interval=interval,
            trend_interval=os.getenv("TREND_INTERVAL", "1h"),
            context_interval=os.getenv("CONTEXT_INTERVAL", "4h"),
            candle_limit=_int("CANDLE_LIMIT", 300, 100),
            candle_close_delay_seconds=_int("CANDLE_CLOSE_DELAY_SECONDS", 20, 0),
            position_poll_seconds=_int("POSITION_POLL_SECONDS", 10, 5),
            max_consecutive_errors=_int("MAX_CONSECUTIVE_ERRORS", 20, 1),
            ema_fast=fast,
            ema_slow=slow,
            atr_period=_int("ATR_PERIOD", 14, 2),
            trend_slope_bars=_int("TREND_SLOPE_BARS", 3, 1),
            minimum_trend_separation_atr=_float(
                "MINIMUM_TREND_SEPARATION_ATR", 0, 0, 10
            ),
            breakout_lookback=_int("BREAKOUT_LOOKBACK", 10, 2),
            impulse_atr=_float("IMPULSE_ATR", 1.0, 0.1, 5),
            pullback_min_bars=minimum,
            pullback_max_bars=maximum,
            pullback_touch_atr=_float("PULLBACK_TOUCH_ATR", 0.25, 0, 2),
            pullback_max_retrace=_float("PULLBACK_MAX_RETRACE", 0.8, 0.2, 2),
            pullback_min_depth=_float("PULLBACK_MIN_DEPTH", 0.35, 0, 2),
            pullback_max_depth=_float("PULLBACK_MAX_DEPTH", 1.15, 0.1, 3),
            confirmation_lookback=_int("CONFIRMATION_LOOKBACK", 2, 1),
            confirmation_close_fraction=_float(
                "CONFIRMATION_CLOSE_FRACTION", 0.65, 0.5, 1
            ),
            stop_atr=_float("STOP_ATR", 1.0, 0.2, 10),
            stop_buffer_atr=_float("STOP_BUFFER_ATR", 0.15, 0, 2),
            reward_risk=_float("REWARD_RISK", 1.5, 1, 10),
            minimum_net_reward_risk=_float("MINIMUM_NET_REWARD_RISK", 1.1, 0.5, 10),
            breakeven_trigger_r=_float("BREAKEVEN_TRIGGER_R", 1.0, 0.5, 10),
            risk_per_trade=_float("RISK_PER_TRADE", 0.005, 0.0001, 0.02),
            max_daily_loss=_float("MAX_DAILY_LOSS", 0.02, 0.001, 0.10),
            max_trades_per_day=_int("MAX_TRADES_PER_DAY", 12, 1),
            max_leverage=_float("MAX_LEVERAGE", 3, 1, 10),
            paper_start_balance=_float("PAPER_START_BALANCE", 500, 10, 1_000_000),
            taker_fee_rate=_float("TAKER_FEE_RATE", 0.0005, 0, 0.01),
            slippage_bps=_float("SLIPPAGE_BPS", 2, 0, 100),
            max_hold_minutes=_int("MAX_HOLD_MINUTES", 105, 5),
            cooldown_minutes=_int("COOLDOWN_MINUTES", 10, 0),
            binance_base_url=os.getenv("BINANCE_BASE_URL", "https://fapi.binance.com").rstrip("/"),
            binance_api_key=os.getenv("BINANCE_API_KEY", ""),
            binance_secret_key=os.getenv("BINANCE_SECRET_KEY", ""),
            require_binance_account=_bool("REQUIRE_BINANCE_ACCOUNT", False),
            request_timeout=_int("REQUEST_TIMEOUT", 15, 1),
            data_dir=Path(os.getenv("DATA_DIR", "/app/data")),
            telegram_token=os.getenv("TELEGRAM_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            log_level=level,
        )

    def validate_secrets(self) -> None:
        if self.require_binance_account and not (self.binance_api_key and self.binance_secret_key):
            raise ValueError("BINANCE_API_KEY and BINANCE_SECRET_KEY are required for account reads")
