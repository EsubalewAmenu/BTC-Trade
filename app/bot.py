import logging
import signal
import sys
import time

from binance_api import BinanceFuturesClient
from csv_ledger import CsvLedger
from llm_decision import request_decision, wait_analysis
from paper_engine import PaperEngine
from settings import Settings
from strategy import Signal, analyze, decide


LOG = logging.getLogger("btc_pullback_trader")
RUNNING = True


def stop(_signum, _frame):
    global RUNNING
    RUNNING = False


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
        config.log_external_responses,
    )
    ledger = CsvLedger(config.data_dir)
    engine = PaperEngine(config, ledger)
    account = client.account_summary() if config.require_binance_account else {}
    if account:
        LOG.info("Connected to Binance account (available USDT: %.2f)", account["available_balance"])
    LOG.info(
        "Pullback strategy: mode=%s, interval=%s, candle_context=%d",
        config.decision_mode, config.interval,
        200 if config.decision_mode == "llm" else config.candle_limit,
    )
    LOG.warning("PAPER MODE: this application contains no order-placement endpoint")

    heartbeat = config.data_dir / "heartbeat"
    consecutive_errors = 0
    while RUNNING:
        try:
            price = client.mark_price(config.symbol)
            result = engine.check_exit(price)
            if result:
                LOG.info("Paper position closed: %s", result)
                client.send_telegram(str(result), config.telegram_token, config.telegram_chat_id)

            if not engine.position:
                if config.decision_mode == "llm":
                    candles = client.klines(config.symbol, config.interval, 201).tail(200)
                    candle_time = candles.iloc[-1].close_time.isoformat()
                    if engine.state["last_decision_candle"] == candle_time:
                        heartbeat.touch()
                        consecutive_errors = 0
                        time.sleep(config.poll_seconds)
                        continue
                    try:
                        decision, analysis = request_decision(candles, config)
                    except Exception as exc:
                        LOG.warning("LLM decision rejected; recording WAIT: %s", exc)
                        decision, analysis = wait_analysis(candles, config, str(exc))
                else:
                    candles = client.klines(config.symbol, config.interval, config.candle_limit)
                    trend = client.klines(config.symbol, config.trend_interval, config.candle_limit)
                    context = client.klines(config.symbol, config.context_interval, config.candle_limit)
                    analysis = analyze(candles, trend, context, config)
                    decision = decide(analysis)
                if engine.state["last_decision_candle"] != analysis.candle_time:
                    allowed, reason = engine.can_open(decision, analysis)
                    engine.mark_decision(analysis, decision, result=reason)
                    LOG.info("%s: %s (%s)", decision.action, decision.rationale, reason)
                    if allowed and decision.action in {Signal.LONG.value, Signal.SHORT.value}:
                        position = engine.open(decision, analysis, price)
                        if position:
                            LOG.info("Opened paper position: %s", position)
                            client.send_telegram(str(position), config.telegram_token, config.telegram_chat_id)
                        else:
                            LOG.info("Skipped signal because next price had crossed invalidation")
            heartbeat.touch()
            consecutive_errors = 0
        except Exception:
            consecutive_errors += 1
            LOG.exception(
                "Paper-trading cycle failed (%d/%d); retrying",
                consecutive_errors, config.max_consecutive_errors,
            )
            if consecutive_errors >= config.max_consecutive_errors:
                LOG.critical("Sustained failures detected; exiting so Docker can restart the bot")
                return 1
        time.sleep(config.poll_seconds)
    LOG.info("Stopped")
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    sys.exit(main())
