#!/usr/bin/env python3
"""Paper or guarded real BTCUSDT pullback trading from Binance Futures charts."""

import argparse
import csv
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_DIR = Path(__file__).resolve().parent
ROOT = BASE_DIR.parent
TRADE_IMAGE_DIR = ROOT / "trade-image"
sys.path.insert(0, str(TRADE_IMAGE_DIR))
sys.path.insert(0, str(BASE_DIR))
from analyze_chart import analyze, load_env  # noqa: E402
from real_trading import BinanceFuturesClient, decimal_floor, weighted_fill  # noqa: E402


BINANCE_URL = "https://www.binance.com/en/futures/BTCUSDT"
FAPI_BASE = "https://fapi.binance.com"
INTERVAL_SECONDS = 15 * 60
STOP_REQUESTED = False
TRADE_FIELDS = (
    "Trade ID", "Status", "Symbol", "Timeframe", "Side", "Opened UTC", "Closed UTC",
    "Signal Candle UTC", "Entry Price", "Exit Price", "Quantity BTC", "Notional USDT",
    "Margin USDT", "Leverage", "Stop Price", "Target Price", "Entry Fee", "Exit Fee",
    "Gross PnL", "Net PnL", "R Multiple", "Exit Reason", "Hold Candles", "Confidence",
    "Rationale", "Entry Screenshot", "Exit Screenshot", "Balance After", "Run ID",
)
SIGNAL_FIELDS = (
    "Trade ID", "Status", "Symbol", "Timeframe", "Side", "Signal Candle UTC",
    "Opened UTC", "Model Entry Price", "Actual Entry Price", "Stop Price", "Target Price",
    "Quantity BTC", "Notional USDT", "Entry Fee", "Reward Risk", "Confidence", "Rationale",
    "Screenshot",
)


def request_stop(_signum=None, _frame=None):
    global STOP_REQUESTED
    STOP_REQUESTED = True


def utc_iso(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat()


def public_get(path: str, params: dict, timeout: int = 20):
    url = f"{FAPI_BASE}{path}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": "btc-paper-trader/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_latest_closed_kline(now_ms=None) -> dict:
    now_ms = now_ms or int(time.time() * 1000)
    rows = public_get("/fapi/v1/klines", {"symbol": "BTCUSDT", "interval": "15m", "limit": 3})
    closed = [row for row in rows if int(row[6]) < now_ms]
    if not closed:
        raise RuntimeError("Binance returned no closed 15-minute candle")
    row = closed[-1]
    return {
        "open_time": int(row[0]), "open": float(row[1]), "high": float(row[2]),
        "low": float(row[3]), "close": float(row[4]), "volume": float(row[5]),
        "close_time": int(row[6]), "quote_volume": float(row[7]), "trades": int(row[8]),
    }


def fetch_mark_price() -> float:
    payload = public_get("/fapi/v1/premiumIndex", {"symbol": "BTCUSDT"})
    return float(payload["markPrice"])


def next_candle_close_time(now=None) -> float:
    now = time.time() if now is None else now
    return (math.floor(now / INTERVAL_SECONDS) + 1) * INTERVAL_SECONDS


def candle_screenshot_time(candle_close_at: float, lead_seconds: float = 10) -> float:
    return candle_close_at - lead_seconds


def interruptible_wait(seconds: float):
    deadline = time.monotonic() + max(0, seconds)
    while not STOP_REQUESTED and time.monotonic() < deadline:
        time.sleep(min(1, deadline - time.monotonic()))


def build_driver(profile_dir: Path):
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError as exc:
        raise RuntimeError("Install dependencies: pip install -r binance-futures/requirements.txt") from exc
    options = Options()
    options.add_argument(f"--user-data-dir={profile_dir.resolve()}")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-renderer-backgrounding")
    return webdriver.Chrome(options=options)


def dismiss_popups(driver):
    from selenium.webdriver.common.by import By
    for selector in ("button[aria-label='Close']", "[data-testid='close-button']", "button[class*='close']"):
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            try:
                if element.is_displayed():
                    element.click()
            except Exception:
                pass


def install_15m_click_listener(driver):
    """Record the user's explicit 15m click instead of guessing login readiness."""
    script = """
        if (!window.__btcPaper15mListenerInstalled) {
          window.__btcPaper15mListenerInstalled = true;
          window.__btcPaper15mClicked = false;
          document.addEventListener('click', (event) => {
            let node = event.target;
            for (let i = 0; node && i < 6; i++, node = node.parentElement) {
              const text = (node.innerText || node.textContent || '').trim();
              if (text === '15m' || text === '15M') {
                window.__btcPaper15mClicked = true;
                break;
              }
            }
          }, true);
        }
    """
    try:
        driver.execute_script(script)
    except Exception:
        pass


def wait_for_user_15m_click(driver):
    last_notice = 0.0
    while not STOP_REQUESTED:
        install_15m_click_listener(driver)
        try:
            if driver.execute_script("return window.__btcPaper15mClicked === true;"):
                print("15m timeframe click detected. Live scheduling started.", flush=True)
                return True
        except Exception:
            pass
        now = time.monotonic()
        if now - last_notice >= 15:
            print("Waiting for you to finish login and click the 15m timeframe...", flush=True)
            last_notice = now
        time.sleep(0.5)
    return False


def activate_browser(driver):
    """Bring Selenium Chrome forward so the user can watch the final ten seconds."""
    try:
        driver.switch_to.window(driver.current_window_handle)
        driver.maximize_window()
        driver.execute_script("window.focus();")
    except Exception:
        pass
    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["osascript", "-e", 'tell application "Google Chrome" to activate'],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
            )
        except Exception:
            pass


def capture_chart(driver, image_path: Path) -> str:
    """Prefer a large chart element; fall back to the viewport."""
    from selenium.webdriver.common.by import By
    candidates = []
    selectors = (
        "[data-testid*='chart']", "[class*='chart-container']", "[class*='chartContainer']",
        "[id*='chart']", "iframe",
    )
    seen = set()
    for selector in selectors:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            try:
                if element.id in seen or not element.is_displayed():
                    continue
                seen.add(element.id)
                size = element.size
                if size["width"] >= 700 and size["height"] >= 350:
                    candidates.append((size["width"] * size["height"], element))
            except Exception:
                pass
    for _area, element in sorted(candidates, reverse=True, key=lambda item: item[0]):
        try:
            if element.screenshot(str(image_path)):
                return "chart-element"
        except Exception:
            continue
    if not driver.save_screenshot(str(image_path)):
        raise RuntimeError("Binance screenshot failed")
    return "viewport"


def write_effective_context(path: Path, base_context: Path, candle: dict, open_trade):
    trade_instruction = "No paper position is open; evaluate a new pullback setup."
    if open_trade:
        trade_instruction = (
            "A paper position is open. Return WAIT with null entry/stop/target; do not propose "
            f"another trade. Open position: {json.dumps(open_trade)}"
        )
    exact = {
        "symbol": "BTCUSDT perpetual", "timeframe": "15m", "open": candle["open"],
        "high": candle["high"], "low": candle["low"], "close": candle["close"],
        "volume": candle["volume"], "open_utc": utc_iso(candle["open_time"]),
        "close_utc": utc_iso(candle["close_time"]),
    }
    text = base_context.read_text(encoding="utf-8") + (
        "\n\nBINANCE LIVE OVERRIDE\nThe screenshot was captured approximately 10 seconds before the "
        "rightmost 15-minute candle closed. Analyze that rightmost near-closed candle and earlier "
        "structure. It has since finalized, and the following Binance Futures API candle is "
        "authoritative; copy its high, low, close and close_utc exactly into the response and do "
        f"not substitute other chart values: {json.dumps(exact)}\n{trade_instruction}\n"
        "This is immediate paper execution: entry_price must be within 0.5% of the authoritative "
        "close and the target must not have been touched by that closed candle."
    )
    path.write_text(text, encoding="utf-8")


def analyze_with_retry(image_path: Path, context_path: Path, args, open_trade):
    for attempt in range(1, args.llm_attempts + 1):
        try:
            return analyze(
                image_path, context_path, args.model, args.llm_timeout, open_trade=None,
                provider="qwen", qwen_host=args.qwen_host, num_ctx=args.num_ctx,
            )
        except Exception as exc:
            if attempt == args.llm_attempts:
                raise
            delay = args.retry_delay * (2 ** (attempt - 1))
            print(f"Qwen attempt {attempt}/{args.llm_attempts} failed: {exc}; retrying in {delay}s", flush=True)
            interruptible_wait(delay)


def append_csv(path: Path, fields, row=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        if needs_header:
            writer.writeheader()
        if row is not None:
            writer.writerow({field: row.get(field, "") for field in fields})


def round_down(value: float, step: float) -> float:
    return math.floor((value + 1e-12) / step) * step


def adverse_fill(price: float, side: str, opening: bool, slippage_bps: float) -> float:
    rate = slippage_bps / 10000
    buy = (side == "LONG") == opening
    return price * (1 + rate if buy else 1 - rate)


def exit_for_candle(trade: dict, candle: dict):
    stop_hit = candle["low"] <= trade["stop"] if trade["side"] == "LONG" else candle["high"] >= trade["stop"]
    target_hit = candle["high"] >= trade["target"] if trade["side"] == "LONG" else candle["low"] <= trade["target"]
    if stop_hit:
        return trade["stop"], "STOP"
    if target_hit:
        return trade["target"], "TARGET"
    return None


def close_trade(trade: dict, candle: dict, screenshot: Path, balance: float, args):
    raw_exit, reason = exit_for_candle(trade, candle)
    exit_price = adverse_fill(raw_exit, trade["side"], False, args.slippage_bps)
    direction = 1 if trade["side"] == "LONG" else -1
    gross = direction * (exit_price - trade["entry"]) * trade["quantity"]
    exit_fee = exit_price * trade["quantity"] * args.taker_fee
    net = gross - trade["entry_fee"] - exit_fee
    risk_amount = abs(trade["entry"] - trade["stop"]) * trade["quantity"]
    balance_after = balance + net
    row = {
        "Trade ID": trade["id"], "Status": "CLOSED", "Symbol": "BTCUSDT", "Timeframe": "15m",
        "Side": trade["side"], "Opened UTC": trade["opened_utc"], "Closed UTC": utc_iso(candle["close_time"]),
        "Signal Candle UTC": trade["signal_candle_utc"], "Entry Price": f"{trade['entry']:.2f}",
        "Exit Price": f"{exit_price:.2f}", "Quantity BTC": f"{trade['quantity']:.3f}",
        "Notional USDT": f"{trade['entry'] * trade['quantity']:.2f}",
        "Margin USDT": f"{trade['entry'] * trade['quantity'] / args.leverage:.2f}", "Leverage": args.leverage,
        "Stop Price": trade["stop"], "Target Price": trade["target"],
        "Entry Fee": f"{trade['entry_fee']:.4f}", "Exit Fee": f"{exit_fee:.4f}",
        "Gross PnL": f"{gross:.4f}", "Net PnL": f"{net:.4f}",
        "R Multiple": f"{net / risk_amount:.4f}", "Exit Reason": reason,
        "Hold Candles": trade["hold_candles"], "Confidence": trade["confidence"],
        "Rationale": trade["rationale"], "Entry Screenshot": trade["entry_screenshot"],
        "Exit Screenshot": str(screenshot.resolve()), "Balance After": f"{balance_after:.4f}",
        "Run ID": trade["run_id"],
    }
    return row, balance_after


def try_open_trade(decision: dict, candle: dict, screenshot: Path, balance: float, run_id: str, step: int, args):
    side = decision["direction"].upper()
    mark = fetch_mark_price()
    entry = adverse_fill(mark, side, True, args.slippage_bps)
    stop, target = float(decision["stop_price"]), float(decision["target_price"])
    valid_geometry = stop < entry < target if side == "LONG" else target < entry < stop
    if not valid_geometry:
        return None, f"live mark fill {entry:.2f} invalidated model geometry"
    rr = abs(target - entry) / abs(entry - stop)
    if rr < 1.999:
        return None, f"live mark fill reduced reward/risk to {rr:.4f}"
    risk_budget = balance * args.risk_percent / 100
    risk_quantity = risk_budget / abs(entry - stop)
    margin_quantity = balance * args.leverage / entry
    quantity = round_down(min(risk_quantity, margin_quantity), args.quantity_step)
    if quantity < args.quantity_step:
        return None, "calculated quantity is below Binance BTCUSDT minimum step"
    trade_id = f"{run_id}-{step:05d}"
    return {
        "id": trade_id, "run_id": run_id, "side": side, "opened_utc": datetime.now(timezone.utc).isoformat(),
        "signal_candle_utc": utc_iso(candle["close_time"]), "entry": entry, "stop": stop,
        "target": target, "quantity": quantity, "entry_fee": entry * quantity * args.taker_fee,
        "confidence": decision["confidence"], "rationale": decision["rationale"],
        "entry_screenshot": str(screenshot.resolve()), "hold_candles": 0,
    }, None


def try_open_real_trade(decision, candle, screenshot, run_id, step, client, rules, args):
    side = decision["direction"].upper()
    mark = fetch_mark_price()
    stop, target = float(decision["stop_price"]), float(decision["target_price"])
    if not (stop < mark < target if side == "LONG" else target < mark < stop):
        return None, f"live mark {mark:.2f} invalidated model geometry"
    if abs(target - mark) / abs(mark - stop) < 1.999:
        return None, "live mark reduced reward/risk below 2.0"
    step_size, min_qty, tick = rules
    quantity = decimal_floor(args.real_notional / mark, step_size)
    if quantity < min_qty:
        return None, f"{args.real_notional:.2f} USDT is below the BTCUSDT minimum market quantity"
    if float(quantity) * mark > args.max_real_notional + 0.01:
        return None, "calculated order exceeds the hard real-notional cap"
    trade_id = f"{run_id}-{step:05d}"
    entry_side, exit_side = ("BUY", "SELL") if side == "LONG" else ("SELL", "BUY")
    entry_order = client.market_order(entry_side, str(quantity), f"pb-{trade_id}-entry")
    entry_trades = client.user_trades(entry_order["orderId"])
    entry, filled_qty, entry_fee, _ = weighted_fill(entry_trades)
    stop = float(decimal_floor(stop, tick))
    target = float(decimal_floor(target, tick))
    try:
        stop_order = client.protective_order(exit_side, "STOP_MARKET", stop, f"pb-{trade_id}-stop")
        target_order = client.protective_order(exit_side, "TAKE_PROFIT_MARKET", target, f"pb-{trade_id}-target")
    except Exception:
        # Never leave a successfully-filled live entry unprotected.
        try:
            if 'stop_order' in locals():
                client.cancel_algo(stop_order["algoId"])
        finally:
            client.market_order(exit_side, str(quantity), f"pb-{trade_id}-emergency", reduce_only=True)
        raise RuntimeError("protective orders failed; the new position was emergency-closed")
    return {
        "id": trade_id, "run_id": run_id, "mode": "real", "side": side,
        "opened_utc": datetime.now(timezone.utc).isoformat(), "opened_ms": int(time.time() * 1000),
        "signal_candle_utc": utc_iso(candle["close_time"]), "entry": entry, "stop": stop,
        "target": target, "quantity": filled_qty, "entry_fee": entry_fee,
        "entry_order_id": entry_order["orderId"], "stop_algo_id": stop_order["algoId"],
        "target_algo_id": target_order["algoId"], "confidence": decision["confidence"],
        "rationale": decision["rationale"], "entry_screenshot": str(screenshot.resolve()),
        "hold_candles": 0,
    }, None


def monitor_real_trade(trade, client, args):
    """Check at candle boundaries; Qwen is deliberately not called while a position is open."""
    print("REAL position tracking resumed. Qwen calls and screenshots are paused while it is open.", flush=True)
    while not STOP_REQUESTED:
        check_at = next_candle_close_time() + 1
        print(f"NEXT POSITION CHECK at 15m candle close in {max(0, check_at - time.time()):.1f}s", flush=True)
        interruptible_wait(check_at - time.time())
        if STOP_REQUESTED:
            return None
        position = api_call_with_retry("position check", client.position_amount)
        if position == 0:
            break
        trade["hold_candles"] = trade.get("hold_candles", 0) + 1
        (BASE_DIR / "open_trade.json").write_text(json.dumps(trade, indent=2), encoding="utf-8")
        print(f"Position remains open ({position} BTC).", flush=True)
    if STOP_REQUESTED:
        return None
    stop_state = api_call_with_retry("stop order status", client.query_algo, trade["stop_algo_id"])
    target_state = api_call_with_retry("target order status", client.query_algo, trade["target_algo_id"])
    stop_triggered = bool(stop_state.get("actualOrderId"))
    target_triggered = bool(target_state.get("actualOrderId"))
    if stop_triggered or target_triggered:
        winning = stop_state if stop_triggered else target_state
        losing = target_state if stop_triggered else stop_state
        api_call_with_retry("cancel unused protective order", client.cancel_algo, losing["algoId"])
        fills = api_call_with_retry("exit fills", client.user_trades, winning["actualOrderId"])
        exit_reason = "STOP" if stop_triggered else "TARGET"
    else:
        # Covers a manual close or manually replaced protective order.
        all_fills = api_call_with_retry("trade history", client.user_trades_since, trade["opened_ms"])
        fills = [item for item in all_fills if int(item["orderId"]) != int(trade["entry_order_id"])]
        if not fills:
            raise RuntimeError("position is flat but no exit fill is available yet; restart to reconcile")
        exit_reason = "MANUAL/OTHER"
    exit_price, _qty, exit_fee, realized = weighted_fill(fills)
    net = realized - trade["entry_fee"] - exit_fee
    return {
        "Trade ID": trade["id"], "Status": "CLOSED", "Symbol": "BTCUSDT", "Timeframe": "15m",
        "Side": trade["side"], "Opened UTC": trade["opened_utc"],
        "Closed UTC": datetime.now(timezone.utc).isoformat(), "Signal Candle UTC": trade["signal_candle_utc"],
        "Entry Price": f"{trade['entry']:.2f}", "Exit Price": f"{exit_price:.2f}",
        "Quantity BTC": f"{trade['quantity']:.3f}",
        "Notional USDT": f"{trade['entry'] * trade['quantity']:.2f}", "Margin USDT": "Binance account",
        "Leverage": "Binance account", "Stop Price": trade["stop"], "Target Price": trade["target"],
        "Entry Fee": f"{trade['entry_fee']:.8f}", "Exit Fee": f"{exit_fee:.8f}",
        "Gross PnL": f"{realized:.8f}", "Net PnL": f"{net:.8f}",
        "R Multiple": "", "Exit Reason": exit_reason, "Hold Candles": trade.get("hold_candles", ""),
        "Confidence": trade["confidence"], "Rationale": trade["rationale"],
        "Entry Screenshot": trade["entry_screenshot"], "Exit Screenshot": "",
        "Balance After": "see Binance", "Run ID": trade["run_id"],
    }


def api_call_with_retry(label, function, *args, attempts=6):
    """Retry temporary TLS/network failures without losing exchange-hosted protection."""
    for attempt in range(1, attempts + 1):
        try:
            return function(*args)
        except Exception as exc:
            if attempt == attempts:
                raise
            delay = min(60, 5 * (2 ** (attempt - 1)))
            print(f"Binance {label} failed ({attempt}/{attempts}): {exc}; retrying in {delay}s", flush=True)
            interruptible_wait(delay)


def run(args) -> int:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    screenshots = args.screenshot_dir
    screenshots.mkdir(parents=True, exist_ok=True)
    args.profile_dir.mkdir(parents=True, exist_ok=True)
    append_csv(BASE_DIR / "trades.csv", TRADE_FIELDS)
    append_csv(BASE_DIR / "signals.csv", SIGNAL_FIELDS)
    context_path = BASE_DIR / "current_context.txt"
    real_client = None
    real_rules = None
    balance = args.initial_balance
    if args.mode == "real":
        if args.confirm_real_trading != "I_UNDERSTAND":
            raise RuntimeError("real mode requires --confirm-real-trading I_UNDERSTAND")
        real_client = BinanceFuturesClient(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_SECRET_KEY"))
        available = real_client.preflight()
        real_rules = real_client.symbol_rules()
        if available <= 0:
            raise RuntimeError("Binance reports no available USDT futures balance")
        print(
            f"REAL MODE ARMED | available={available:.2f} USDT | requested notional="
            f"{args.real_notional:.2f} USDT | hard cap={args.max_real_notional:.2f} USDT",
            flush=True,
        )
        if args.preflight_only:
            print("Authenticated read-only preflight passed; no order was placed.", flush=True)
            return 0
    driver = build_driver(args.profile_dir)
    open_trade, closed_trades, step, last_open_time = None, 0, 0, None
    try:
        driver.get(args.url)
        install_15m_click_listener(driver)
        print("Binance Futures opened. Log in and arrange the chart, then click the 15m timeframe.", flush=True)
        if not wait_for_user_15m_click(driver):
            return 0
        dismiss_popups(driver)
        while not STOP_REQUESTED and closed_trades < args.trades:
            candle_close_at = next_candle_close_time()
            print(f"NEXT 15m CANDLE CLOSE in {max(0, candle_close_at - time.time()):.1f}s", flush=True)
            screenshot_at = candle_screenshot_time(candle_close_at, args.screenshot_lead)
            # Bring Binance forward ten seconds before capture, preserving the
            # existing visible-market preview without showing the next candle.
            preview_at = screenshot_at - 10
            interruptible_wait(preview_at - time.time())
            if STOP_REQUESTED:
                break
            activate_browser(driver)
            print(
                f"Binance brought forward; screenshot in 10s, approximately "
                f"{args.screenshot_lead:.0f}s before candle close...",
                flush=True,
            )
            interruptible_wait(screenshot_at - time.time())
            if STOP_REQUESTED:
                break
            step += 1
            dismiss_popups(driver)
            image_path = screenshots / f"step_{run_id}_{step:05d}.png"
            capture_mode = capture_chart(driver, image_path)
            shutil.copyfile(image_path, screenshots / "last_screenshot.png")
            print(f"STEP {step}: screenshot captured before close; waiting for finalized Binance candle...", flush=True)
            interruptible_wait(candle_close_at + 1 - time.time())
            if STOP_REQUESTED:
                break
            candle = fetch_latest_closed_kline()
            if candle["open_time"] == last_open_time:
                image_path.unlink(missing_ok=True)
                continue
            last_open_time = candle["open_time"]
            write_effective_context(context_path, args.context, candle, open_trade)
            print(
                f"STEP {step}: {utc_iso(candle['close_time'])} closed O={candle['open']} H={candle['high']} "
                f"L={candle['low']} C={candle['close']} | screenshot={capture_mode} | requesting QWEN...",
                flush=True,
            )
            position_was_open = open_trade is not None
            try:
                decision, _raw = analyze_with_retry(image_path, context_path, args, open_trade)
            except Exception as exc:
                print(f"STEP {step}: QWEN error: {exc}", file=sys.stderr, flush=True)
                if not position_was_open:
                    image_path.unlink(missing_ok=True)
                if not args.continue_on_error:
                    return 1
                continue
            decision.update({
                "candle_high": candle["high"], "candle_low": candle["low"],
                "candle_close": candle["close"], "candle_utc": utc_iso(candle["close_time"]),
            })
            closed_this_candle = False
            if open_trade:
                open_trade["hold_candles"] += 1
                if exit_for_candle(open_trade, candle):
                    row, balance = close_trade(open_trade, candle, image_path, balance, args)
                    append_csv(BASE_DIR / "trades.csv", TRADE_FIELDS, row)
                    print(
                        f"STEP {step}: {open_trade['id']} closed {row['Exit Reason']} | "
                        f"net={row['Net PnL']} USDT | balance={row['Balance After']}", flush=True,
                    )
                    open_trade, closed_this_candle = None, True
                    closed_trades += 1
                    (BASE_DIR / "open_trade.json").unlink(missing_ok=True)
                    print(f"Completed trades: {closed_trades}/{args.trades}", flush=True)
                else:
                    (BASE_DIR / "open_trade.json").write_text(json.dumps(open_trade, indent=2), encoding="utf-8")
            if not open_trade and not closed_this_candle and decision["direction"].upper() in {"LONG", "SHORT"}:
                if args.mode == "real":
                    open_trade, rejection = try_open_real_trade(
                        decision, candle, image_path, run_id, step, real_client, real_rules, args
                    )
                else:
                    open_trade, rejection = try_open_trade(
                        decision, candle, image_path, balance, run_id, step, args
                    )
                if rejection:
                    decision.update({
                        "direction": "WAIT", "entry_price": None, "stop_price": None,
                        "target_price": None, "confidence": 95,
                        "rationale": f"Live paper signal rejected: {rejection}",
                    })
                else:
                    (BASE_DIR / "open_trade.json").write_text(json.dumps(open_trade, indent=2), encoding="utf-8")
                    rr = abs(open_trade["target"] - open_trade["entry"]) / abs(open_trade["entry"] - open_trade["stop"])
                    append_csv(BASE_DIR / "signals.csv", SIGNAL_FIELDS, {
                        "Trade ID": open_trade["id"], "Status": "SIGNAL", "Symbol": "BTCUSDT",
                        "Timeframe": "15m", "Side": open_trade["side"],
                        "Signal Candle UTC": open_trade["signal_candle_utc"],
                        "Opened UTC": open_trade["opened_utc"], "Model Entry Price": decision["entry_price"],
                        "Actual Entry Price": f"{open_trade['entry']:.2f}", "Stop Price": open_trade["stop"],
                        "Target Price": open_trade["target"], "Quantity BTC": f"{open_trade['quantity']:.3f}",
                        "Notional USDT": f"{open_trade['entry'] * open_trade['quantity']:.2f}",
                        "Entry Fee": f"{open_trade['entry_fee']:.4f}", "Reward Risk": f"{rr:.4f}",
                        "Confidence": open_trade["confidence"], "Rationale": open_trade["rationale"],
                        "Screenshot": open_trade["entry_screenshot"],
                    })
                    print(
                        f"STEP {step}: {args.mode.upper()} {open_trade['side']} opened at fill {open_trade['entry']:.2f} | "
                        f"qty={open_trade['quantity']:.3f} BTC | fee={open_trade['entry_fee']:.4f}", flush=True,
                    )
                    if args.mode == "real":
                        row = monitor_real_trade(open_trade, real_client, args)
                        if row is None:
                            print("Stopping monitor; Binance protective stop and target remain active.", flush=True)
                            break
                        append_csv(BASE_DIR / "trades.csv", TRADE_FIELDS, row)
                        print(
                            f"{open_trade['id']} closed {row['Exit Reason']} | actual net={row['Net PnL']} USDT | "
                            f"entry fee={row['Entry Fee']} | exit fee={row['Exit Fee']}", flush=True,
                        )
                        open_trade = None
                        closed_this_candle = True
                        closed_trades += 1
                        (BASE_DIR / "open_trade.json").unlink(missing_ok=True)
                        print(f"Completed trades: {closed_trades}/{args.trades}", flush=True)
            keep = position_was_open or open_trade is not None or closed_this_candle
            if keep:
                (screenshots / f"decision_{run_id}_{step:05d}.json").write_text(
                    json.dumps({"step": step, "decision": decision, "candle": candle}, indent=2), encoding="utf-8"
                )
            else:
                image_path.unlink(missing_ok=True)
            print(
                f"STEP {step}: {decision['direction']} confidence={decision['confidence']} | "
                f"{decision['rationale']}", flush=True,
            )
        return 0
    finally:
        context_path.unlink(missing_ok=True)
        driver.quit()
        print(f"Screenshots: {screenshots} | Signals: {BASE_DIR / 'signals.csv'} | Trades: {BASE_DIR / 'trades.csv'}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=BINANCE_URL)
    parser.add_argument("--trades", type=int, default=1)
    parser.add_argument("--mode", choices=("paper", "real"), default="paper")
    parser.add_argument("--real-notional", type=float, default=100.0,
                        help="desired live entry notional in USDT (real mode only)")
    parser.add_argument("--max-real-notional", type=float, default=100.0,
                        help="hard live-order notional ceiling")
    parser.add_argument("--confirm-real-trading", default="",
                        help="real mode safety phrase: I_UNDERSTAND")
    parser.add_argument("--position-poll-seconds", type=float, default=5.0)
    parser.add_argument("--preflight-only", action="store_true",
                        help="validate real-mode account access and safety state without opening Chrome or placing orders")
    parser.add_argument("--screenshot-lead", type=float, default=10)
    parser.add_argument("--initial-balance", type=float, default=1000)
    parser.add_argument("--risk-percent", type=float, default=0.5)
    parser.add_argument("--leverage", type=float, default=1)
    parser.add_argument("--taker-fee", type=float, default=0.0005)
    parser.add_argument("--slippage-bps", type=float, default=1)
    parser.add_argument("--quantity-step", type=float, default=0.001)
    parser.add_argument("--model", default=os.getenv("QWEN_MODEL", "qwen3-vl:8b-instruct"))
    parser.add_argument("--qwen-host", default=os.getenv("QWEN_HOST", "http://127.0.0.1:11434"))
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--llm-timeout", type=int, default=300)
    parser.add_argument("--llm-attempts", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=5)
    parser.add_argument("--context", type=Path, default=TRADE_IMAGE_DIR / "qwen_system_context.txt")
    parser.add_argument("--profile-dir", type=Path, default=BASE_DIR / "chrome-profile")
    parser.add_argument("--screenshot-dir", type=Path, default=BASE_DIR / "screenshots")
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()
    if any((args.trades < 1, args.screenshot_lead < 0, args.initial_balance <= 0, args.risk_percent <= 0,
            args.leverage <= 0, args.taker_fee < 0, args.slippage_bps < 0, args.quantity_step <= 0)):
        parser.error("invalid non-positive trading/runtime configuration")
    if args.real_notional <= 0 or args.max_real_notional <= 0 or args.position_poll_seconds <= 0:
        parser.error("real notional, cap, and position polling interval must be positive")
    if args.real_notional > args.max_real_notional:
        parser.error("--real-notional cannot exceed --max-real-notional")
    load_env(ROOT / ".env")
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        return run(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
