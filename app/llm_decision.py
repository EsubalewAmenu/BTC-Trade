import json
import logging
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

import pandas as pd

from strategy import Decision, Signal, atr


LOG = logging.getLogger("btc_pullback_trader.llm")


def post_json(url: str, payload: dict, timeout: int):
    import requests

    response = requests.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


@dataclass(frozen=True)
class LlmAnalysis:
    symbol: str
    interval: str
    candle_time: str
    close: float
    atr: float
    trend: str
    trend_filter: str
    context_trend: str
    impulse_found: bool
    pullback_found: bool
    confirmation_found: bool
    pullback_bars: int
    pullback_depth: float
    breakout_level: float | None
    invalidation_price: float | None
    target_price: float | None
    confidence: float
    rule_signal: str
    rule_reason: str


def candle_context(candles: pd.DataFrame) -> str:
    if len(candles) != 200:
        raise ValueError(f"LLM context requires exactly 200 closed candles, got {len(candles)}")
    frame = candles.copy()
    frame["ema_50"] = frame["close"].ewm(span=50, adjust=False).mean()
    renamed = frame.rename(columns={
        "trades": "count",
        "taker_base": "taker_buy_volume",
        "taker_quote": "taker_buy_quote_volume",
    })
    columns = [
        "open_time", "open", "high", "low", "close", "volume", "ema_50",
        "close_time", "quote_volume", "count", "taker_buy_volume",
        "taker_buy_quote_volume", "ignore",
    ]
    for name in columns:
        if name not in renamed:
            renamed[name] = 0
    for name in ("open_time", "close_time"):
        renamed[name] = pd.to_datetime(renamed[name], utc=True).astype("int64") // 1_000_000
    output = StringIO()
    renamed[columns].to_csv(output, index=False, lineterminator="\n")
    return output.getvalue()


def _json_object(value):
    for _ in range(3):
        if isinstance(value, dict):
            for key in ("body", "output", "result", "content"):
                if key in value and len(value) == 1:
                    value = value[key]
                    break
            else:
                return value
        elif isinstance(value, str):
            text = value.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            value = json.loads(text)
        else:
            break
    if not isinstance(value, dict):
        raise ValueError("LLM response is not a JSON object")
    return value


def request_decision(candles: pd.DataFrame, config) -> tuple[Decision, LlmAnalysis]:
    context = candle_context(candles)
    system_context = Path(__file__).with_name("field_guide.txt").read_text()
    LOG.info(
        "LLM REQUEST endpoint=%s candles=%d latest_closed_candle=%s",
        config.llm_endpoint_url, len(candles),
        pd.Timestamp(candles.iloc[-1].close_time).isoformat(),
    )
    raw_response = post_json(
        config.llm_endpoint_url,
        {"system_context": system_context, "user_context": context},
        config.llm_timeout_seconds,
    )
    if getattr(config, "log_external_responses", False):
        LOG.info(
            "LLM RAW RESPONSE body=%s",
            raw_response if isinstance(raw_response, str) else json.dumps(raw_response),
        )
    payload = _json_object(raw_response)
    # if getattr(config, "log_external_responses", False):
    #     LOG.info("LLM PARSED RESPONSE body=%s", json.dumps(payload, sort_keys=True))
    direction = str(payload.get("direction", "")).upper()
    if direction not in {Signal.WAIT.value, Signal.LONG.value, Signal.SHORT.value}:
        raise ValueError("LLM direction must be WAIT, LONG, or SHORT")
    confidence = float(payload.get("confidence", 0))
    if not 0 <= confidence <= 100:
        raise ValueError("LLM confidence must be between 0 and 100")
    rationale = str(payload.get("rationale", "")).strip()
    if not rationale:
        raise ValueError("LLM rationale is required")
    final = candles.iloc[-1]
    candle_time = pd.Timestamp(final.close_time).isoformat()
    supplied_time = payload.get("signal_candle_utc")
    if supplied_time and pd.Timestamp(supplied_time) != pd.Timestamp(final.close_time):
        raise ValueError("LLM signal candle does not match the latest supplied candle")
    entry = stop = target = None
    if direction != Signal.WAIT.value:
        entry = float(payload["entry_price"])
        stop = float(payload["stop_price"])
        target = float(payload["target_price"])
        valid = stop < entry < target if direction == Signal.LONG.value else target < entry < stop
        if not valid:
            raise ValueError("LLM stop/entry/target geometry is invalid")
        risk = abs(entry - stop)
        if abs(target - entry) / risk < 2:
            raise ValueError("LLM proposed reward-to-risk is below 2.0")
        deviation_bps = abs(entry - float(final.close)) / float(final.close) * 10_000
        if deviation_bps > config.llm_max_entry_deviation_bps:
            raise ValueError("LLM entry is too far from the latest close")
    current_atr = float(atr(candles, config.atr_period).iloc[-1])
    analysis = LlmAnalysis(
        symbol=config.symbol, interval=config.interval, candle_time=candle_time,
        close=float(final.close), atr=current_atr, trend="LLM", trend_filter="LLM",
        context_trend="LLM", impulse_found=direction != Signal.WAIT.value,
        pullback_found=direction != Signal.WAIT.value,
        confirmation_found=direction != Signal.WAIT.value, pullback_bars=0,
        pullback_depth=0.0, breakout_level=None, invalidation_price=stop,
        target_price=target, confidence=confidence, rule_signal=direction,
        rule_reason=rationale,
    )
    return Decision(direction, rationale), analysis


def wait_analysis(candles: pd.DataFrame, config, reason: str) -> tuple[Decision, LlmAnalysis]:
    final = candles.iloc[-1]
    rationale = f"LLM response rejected: {reason}"
    analysis = LlmAnalysis(
        symbol=config.symbol, interval=config.interval,
        candle_time=pd.Timestamp(final.close_time).isoformat(), close=float(final.close),
        atr=float(atr(candles, config.atr_period).iloc[-1]), trend="LLM",
        trend_filter="LLM", context_trend="LLM", impulse_found=False,
        pullback_found=False, confirmation_found=False, pullback_bars=0,
        pullback_depth=0.0, breakout_level=None, invalidation_price=None,
        target_price=None, confidence=0.0, rule_signal=Signal.WAIT.value,
        rule_reason=rationale,
    )
    return Decision(Signal.WAIT.value, rationale), analysis
