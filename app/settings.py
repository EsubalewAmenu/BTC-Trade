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
    rsi_period: int
    stoch_period: int
    smooth_k: int
    smooth_d: int
    oversold: float
    overbought: float
    ema_fast: int
    ema_slow: int
    atr_period: int
    stop_atr: float
    reward_risk: float
    risk_per_trade: float
    max_daily_loss: float
    max_trades_per_day: int
    max_leverage: float
    paper_start_balance: float
    taker_fee_rate: float
    slippage_bps: float
    min_llm_confidence: float
    max_hold_minutes: int
    cooldown_minutes: int
    binance_base_url: str
    binance_api_key: str
    binance_secret_key: str
    require_binance_account: bool
    openai_api_key: str
    openai_model: str
    gemini_api_key: str
    gemini_model: str
    use_gemini: bool
    require_llm: bool
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
        oversold = _float("STOCH_OVERSOLD", 20, 0, 100)
        overbought = _float("STOCH_OVERBOUGHT", 80, 0, 100)
        if oversold >= overbought:
            raise ValueError("STOCH_OVERSOLD must be below STOCH_OVERBOUGHT")
        level = os.getenv("LOG_LEVEL", "INFO").upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("LOG_LEVEL is invalid")
        symbol = os.getenv("TRADE_SYMBOL", "BTCUSDT").upper()
        if symbol != "BTCUSDT":
            raise ValueError("This paper trader intentionally supports BTCUSDT only")
        return cls(
            symbol=symbol,
            interval=os.getenv("TRADE_INTERVAL", "15m"),
            trend_interval=os.getenv("TREND_INTERVAL", "1h"),
            candle_limit=_int("CANDLE_LIMIT", 250, 100),
            poll_seconds=_int("POLL_SECONDS", 15, 5),
            rsi_period=_int("RSI_PERIOD", 14, 2),
            stoch_period=_int("STOCH_PERIOD", 14, 2),
            smooth_k=_int("STOCH_K", 3, 1),
            smooth_d=_int("STOCH_D", 3, 1),
            oversold=oversold,
            overbought=overbought,
            ema_fast=fast,
            ema_slow=slow,
            atr_period=_int("ATR_PERIOD", 14, 2),
            stop_atr=_float("STOP_ATR", 1.2, 0.2, 10),
            reward_risk=_float("REWARD_RISK", 1.8, 1, 10),
            risk_per_trade=_float("RISK_PER_TRADE", 0.005, 0.0001, 0.02),
            max_daily_loss=_float("MAX_DAILY_LOSS", 0.02, 0.001, 0.10),
            max_trades_per_day=_int("MAX_TRADES_PER_DAY", 4, 1),
            max_leverage=_float("MAX_LEVERAGE", 3, 1, 10),
            paper_start_balance=_float("PAPER_START_BALANCE", 500, 10, 1_000_000),
            taker_fee_rate=_float("TAKER_FEE_RATE", 0.0005, 0, 0.01),
            slippage_bps=_float("SLIPPAGE_BPS", 2, 0, 100),
            min_llm_confidence=_float("MIN_LLM_CONFIDENCE", 0.70, 0.5, 1),
            max_hold_minutes=_int("MAX_HOLD_MINUTES", 240, 15),
            cooldown_minutes=_int("COOLDOWN_MINUTES", 30, 0),
            binance_base_url=os.getenv("BINANCE_BASE_URL", "https://fapi.binance.com").rstrip("/"),
            binance_api_key=os.getenv("BINANCE_API_KEY", ""),
            binance_secret_key=os.getenv("BINANCE_SECRET_KEY", ""),
            require_binance_account=_bool("REQUIRE_BINANCE_ACCOUNT", True),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.7-flash"),
            use_gemini=_bool("USE_GEMINI", False),
            require_llm=_bool("REQUIRE_LLM", True),
            request_timeout=_int("REQUEST_TIMEOUT", 15, 1),
            data_dir=Path(os.getenv("DATA_DIR", "/app/data")),
            telegram_token=os.getenv("TELEGRAM_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            log_level=level,
        )

    def validate_secrets(self) -> None:
        if self.require_binance_account and not (self.binance_api_key and self.binance_secret_key):
            raise ValueError("BINANCE_API_KEY and BINANCE_SECRET_KEY are required for account reads")
        if self.require_llm and self.use_gemini and not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required when USE_GEMINI=true")
        if self.require_llm and not self.use_gemini and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when USE_GEMINI=false")
