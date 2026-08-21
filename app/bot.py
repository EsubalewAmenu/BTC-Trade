import logging
import signal
import sys
import time

from binance_api import BinanceFuturesClient
from csv_ledger import CsvLedger
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
    )
    ledger = CsvLedger(config.data_dir)
    engine = PaperEngine(config, ledger)
    account = client.account_summary() if config.require_binance_account else {}
    if account:
        LOG.info("Connected to Binance account (available USDT: %.2f)", account["available_balance"])
    LOG.info(
        "Deterministic pullback strategy: %s entries, %s trend, EMA %d/%d",
        config.interval, config.trend_interval, config.ema_fast, config.ema_slow,
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
                candles = client.klines(config.symbol, config.interval, config.candle_limit)
                trend = client.klines(config.symbol, config.trend_interval, config.candle_limit)
                analysis = analyze(candles, trend, config)
                if engine.state["last_decision_candle"] != analysis.candle_time:
                    decision = decide(analysis)
                    allowed, reason = engine.can_open(decision, analysis)
                    engine.mark_decision(analysis, decision, result=reason)
                    LOG.info("%s: %s (%s)", decision.action, decision.rationale, reason)
                    if allowed and decision.action in {Signal.LONG.value, Signal.SHORT.value}:
                        position = engine.open(decision, analysis, price)
                        LOG.info("Opened paper position: %s", position)
                        client.send_telegram(str(position), config.telegram_token, config.telegram_chat_id)
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
