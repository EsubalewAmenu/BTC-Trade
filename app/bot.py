import logging
import signal
import time

from binance_api import BinanceFuturesClient
from excel_ledger import ExcelLedger
from llm_decider import LLMDecider
from paper_engine import PaperEngine
from settings import Settings
from strategy import Signal, analyze


LOG = logging.getLogger("btc_paper_trader")
RUNNING = True


def stop(_signum, _frame):
    global RUNNING
    RUNNING = False


def settings_rows(config):
    return [
        ["Symbol", config.symbol], ["Execution timeframe", config.interval],
        ["Trend timeframe", config.trend_interval], ["Paper start balance", config.paper_start_balance],
        ["Risk per trade", config.risk_per_trade], ["Max daily loss", config.max_daily_loss],
        ["Max trades/day", config.max_trades_per_day], ["Max leverage", config.max_leverage],
        ["Stop ATR", config.stop_atr], ["Reward/risk", config.reward_risk],
        ["Taker fee rate", config.taker_fee_rate], ["Slippage bps", config.slippage_bps],
        ["Max hold minutes", config.max_hold_minutes],
        ["Minimum LLM confidence", config.min_llm_confidence],
        ["LLM model", config.gemini_model if config.use_gemini else config.openai_model],
        ["LLM provider", "Gemini" if config.use_gemini else "OpenAI"],
    ]


def main():
    config = Settings.from_env()
    config.validate_secrets()
    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    client = BinanceFuturesClient(
        config.binance_base_url, config.request_timeout,
        config.binance_api_key, config.binance_secret_key,
    )
    ledger = ExcelLedger(config.data_dir, config.paper_start_balance, settings_rows(config))
    engine = PaperEngine(config, ledger)
    decider = LLMDecider(
        config.openai_api_key, config.openai_model,
        config.gemini_api_key, config.gemini_model,
        config.use_gemini, config.require_llm,
    )
    account = client.account_summary() if config.binance_api_key else {}
    LOG.info("Connected to Binance account (available USDT: %.2f)", account.get("available_balance", 0))
    LOG.info("LLM provider: %s", decider.provider)
    try:
        verification = decider.verify_connection()
        LOG.info("LLM startup check passed: %s", verification.rationale)
    except Exception as exc:
        LOG.exception("LLM startup check failed for %s: %s", decider.provider, type(exc).__name__)
    LOG.warning("PAPER MODE: no Binance order endpoint exists in this application")

    while RUNNING:
        try:
            price = client.mark_price(config.symbol)
            exit_result = engine.check_exit(price)
            if exit_result:
                LOG.info("Paper position closed: %s", exit_result)
                client.send_telegram(str(exit_result), config.telegram_token, config.telegram_chat_id)

            if not engine.position:
                candles = client.klines(config.symbol, config.interval, config.candle_limit)
                trend = client.klines(config.symbol, config.trend_interval, config.candle_limit)
                analysis = analyze(candles, trend, config)
                if engine.state["last_decision_candle"] != analysis.candle_time:
                    account = client.account_summary() if config.binance_api_key else {}
                    if analysis.rule_signal == Signal.WAIT.value:
                        from llm_decider import Decision
                        decision = Decision("WAIT", 0, f"[rules] {analysis.rule_reason}; LLM not called")
                    else:
                        decision = decider.decide(analysis)
                    allowed, reason = engine.can_open(decision, analysis)
                    engine.mark_decision(analysis, decision, account, reason)
                    LOG.info("%s %.0f%%: %s (%s)", decision.action, decision.confidence * 100, decision.rationale, reason)
                    if allowed:
                        position = engine.open(decision, analysis, price)
                        LOG.info("Opened paper position: %s", position)
                        client.send_telegram(str(position), config.telegram_token, config.telegram_chat_id)
        except Exception:
            LOG.exception("Paper-trading cycle failed; retrying")
        time.sleep(config.poll_seconds)
    LOG.info("Stopped")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    main()
