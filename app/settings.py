import os
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Settings:
    symbol: str
    interval: str
    trend_interval: str
    candle_limit: int
    poll_seconds: int
    max_consecutive_errors: int
    ema_fast: int
    ema_slow: int
    atr_period: int
    trend_slope_bars: int
    impulse_lookback: int
    impulse_atr: float
    pullback_min_bars: int
    pullback_max_bars: int
    pullback_touch_atr: float
    stop_atr: float
    reward_risk: float
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
        return cls(
            symbol=symbol,
            interval=os.getenv("TRADE_INTERVAL", "5m"),
            trend_interval=os.getenv("TREND_INTERVAL", "15m"),
            candle_limit=_int("CANDLE_LIMIT", 300, 100),
            poll_seconds=_int("POLL_SECONDS", 10, 5),
            max_consecutive_errors=_int("MAX_CONSECUTIVE_ERRORS", 20, 1),
            ema_fast=fast,
            ema_slow=slow,
            atr_period=_int("ATR_PERIOD", 14, 2),
            trend_slope_bars=_int("TREND_SLOPE_BARS", 3, 1),
            impulse_lookback=_int("IMPULSE_LOOKBACK", 8, 2),
            impulse_atr=_float("IMPULSE_ATR", 1.0, 0.1, 5),
            pullback_min_bars=minimum,
            pullback_max_bars=maximum,
            pullback_touch_atr=_float("PULLBACK_TOUCH_ATR", 0.25, 0, 2),
            stop_atr=_float("STOP_ATR", 1.0, 0.2, 10),
            reward_risk=_float("REWARD_RISK", 1.5, 1, 10),
            risk_per_trade=_float("RISK_PER_TRADE", 0.005, 0.0001, 0.02),
            max_daily_loss=_float("MAX_DAILY_LOSS", 0.02, 0.001, 0.10),
            max_trades_per_day=_int("MAX_TRADES_PER_DAY", 12, 1),
            max_leverage=_float("MAX_LEVERAGE", 3, 1, 10),
            paper_start_balance=_float("PAPER_START_BALANCE", 500, 10, 1_000_000),
            taker_fee_rate=_float("TAKER_FEE_RATE", 0.0005, 0, 0.01),
            slippage_bps=_float("SLIPPAGE_BPS", 2, 0, 100),
            max_hold_minutes=_int("MAX_HOLD_MINUTES", 60, 5),
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
