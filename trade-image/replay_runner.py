#!/usr/bin/env python3
"""Advance a TradingView bar replay and analyze each rendered chart with Gemini."""

import argparse
import csv
import json
import os
import re
import shutil
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from analyze_chart import DEFAULT_CONTEXT, ROOT, analyze, load_env


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_URL = "https://www.tradingview.com/chart/qmEGf9UB/?symbol=CRYPTO%3ABTCUSD"
FORWARD_SELECTORS = (
    "[title='Forward']",
    "[title*='Forward']",
    "[aria-label*='Forward']",
    "[data-tooltip*='Forward']",
    "[data-name*='forward']",
)
STOP_REQUESTED = False
SIGNAL_FIELDS = (
    "Trade ID", "Status", "Side", "Signal UTC", "Entry Price", "Stop Price",
    "Target Price", "Reward Risk", "Confidence", "Rationale", "Screenshot",
    "Run ID", "Step",
)
TRADE_FIELDS = (
    "Trade ID", "Status", "Side", "Opened UTC", "Closed UTC", "Entry Price",
    "Exit Price", "Quantity BTC", "Notional USDT", "Stop Price", "Target Price",
    "Entry Fee", "Exit Fee", "Gross PnL", "Net PnL", "R Multiple", "Exit Reason",
    "Hold Candles", "Confidence", "Rationale", "Entry Screenshot", "Exit Screenshot",
    "Balance After", "Run ID",
)


def request_stop(_signum=None, _frame=None) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def is_forward_ready(element) -> bool:
    classes = (element.get_attribute("class") or "").split()
    aria_disabled = (element.get_attribute("aria-disabled") or "").lower()
    return element.is_displayed() and element.is_enabled() and "isDisabled" not in classes and aria_disabled != "true"


def cached_forward_ready(element) -> bool:
    try:
        return element is not None and is_forward_ready(element)
    except Exception:
        # TradingView occasionally replaces the complete replay toolbar.
        return False


def find_forward(driver):
    from selenium.webdriver.common.by import By

    seen = set()
    for selector in FORWARD_SELECTORS:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            if element.id not in seen and is_forward_ready(element):
                return element
            seen.add(element.id)

    # TradingView may remove tooltip attributes after the first click. Fall back
    # to the visible accessible text, then return its nearest clickable ancestor.
    xpath = (
        "//*[normalize-space(.)='Forward' or "
        "contains(translate(@title,'FORWARD','forward'),'forward') or "
        "contains(translate(@aria-label,'FORWARD','forward'),'forward')]"
    )
    for element in driver.find_elements(By.XPATH, xpath):
        candidates = [element]
        candidates.extend(element.find_elements(By.XPATH, "ancestor::*[self::button or @role='button'][1]"))
        for candidate in candidates:
            if candidate.id not in seen and is_forward_ready(candidate):
                return candidate
            seen.add(candidate.id)
    return None


def forward_diagnostics(driver) -> str:
    """Return safe control metadata without dumping the full TradingView page."""
    script = """
        const nodes = [...document.querySelectorAll('*')].filter((el) => {
          const value = [el.title, el.getAttribute('aria-label'),
            el.getAttribute('data-tooltip'), el.getAttribute('data-name'),
            el.textContent && el.textContent.trim()].filter(Boolean).join(' ');
          return /forward/i.test(value) && el.offsetParent !== null;
        }).slice(0, 10);
        return nodes.map((el) => ({
          tag: el.tagName,
          title: el.title || null,
          ariaLabel: el.getAttribute('aria-label'),
          dataTooltip: el.getAttribute('data-tooltip'),
          dataName: el.getAttribute('data-name'),
          className: typeof el.className === 'string' ? el.className : null,
          text: (el.textContent || '').trim().slice(0, 80),
          disabled: Boolean(el.disabled),
          ariaDisabled: el.getAttribute('aria-disabled')
        }));
    """
    try:
        return json.dumps(driver.execute_script(script), ensure_ascii=False)
    except Exception as exc:
        return f"diagnostics unavailable: {exc}"


def dismiss_popups(driver) -> None:
    """Best-effort close for TradingView promotional dialogs."""
    from selenium.webdriver.common.by import By

    selectors = (
        "button[aria-label='Close']",
        "[data-name='close']",
        "button[title='Close']",
    )
    for selector in selectors:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            try:
                if element.is_displayed():
                    element.click()
            except Exception:
                pass


def wait_for_replay(driver, timeout: int):
    deadline = time.monotonic() + timeout
    last_notice = 0.0
    while not STOP_REQUESTED and time.monotonic() < deadline:
        dismiss_popups(driver)
        forward = find_forward(driver)
        if forward is not None:
            return forward
        now = time.monotonic()
        if now - last_notice >= 15:
            print("Waiting for an enabled TradingView Replay Forward button...", flush=True)
            last_notice = now
        time.sleep(1)
    details = forward_diagnostics(driver)
    raise TimeoutError(
        f"Replay Forward did not become enabled within {timeout}s. "
        f"Visible Forward-like controls: {details}"
    )


def click_forward(driver, element) -> None:
    try:
        element.click()
    except Exception:
        dismiss_popups(driver)
        driver.execute_script("arguments[0].click();", element)


def save_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def is_retryable_gemini_error(exc: Exception) -> bool:
    message = str(exc)
    return "Gemini HTTP 429" in message or any(
        f"Gemini HTTP {code}" in message for code in (500, 502, 503, 504)
    ) or "Gemini request failed" in message


def gemini_retry_delay(exc: Exception, fallback: float) -> float:
    """Honor retry timing returned by Gemini, with a small safety margin."""
    message = str(exc)
    matches = re.findall(
        r'(?:retryDelay["\']?\s*:\s*["\']?|retry in\s+)([0-9]+(?:\.[0-9]+)?)s',
        message,
        flags=re.IGNORECASE,
    )
    return max([fallback, *(float(value) + 1.0 for value in matches)])


def analyze_with_retry(image_path: Path, args, open_trade=None):
    for attempt in range(1, args.gemini_attempts + 1):
        try:
            return analyze(image_path, args.context, args.model, args.gemini_timeout, open_trade)
        except Exception as exc:
            if attempt >= args.gemini_attempts or not is_retryable_gemini_error(exc):
                raise
            delay = gemini_retry_delay(exc, args.retry_delay * (2 ** (attempt - 1)))
            print(
                f"Gemini attempt {attempt}/{args.gemini_attempts} failed: {exc}. "
                f"Retrying in {delay:.1f}s...",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)


def append_signal_csv(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=SIGNAL_FIELDS)
        if needs_header:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in SIGNAL_FIELDS})


def append_trade_csv(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=TRADE_FIELDS)
        if needs_header:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in TRADE_FIELDS})


def initialize_trade_csv(path: Path) -> None:
    if path.exists() and path.stat().st_size:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        csv.DictWriter(stream, fieldnames=TRADE_FIELDS).writeheader()


def exit_for_candle(trade: dict, high: float, low: float):
    side, stop, target = trade["side"], trade["stop"], trade["target"]
    stop_hit = low <= stop if side == "LONG" else high >= stop
    target_hit = high >= target if side == "LONG" else low <= target
    if stop_hit:  # Conservative when both occur in one replay candle.
        return stop, "STOP"
    if target_hit:
        return target, "TARGET"
    return None


def close_trade_row(trade: dict, decision: dict, image_path: Path, exit_price: float,
                    reason: str, balance: float, fee_rate: float) -> tuple[dict, float]:
    direction = 1 if trade["side"] == "LONG" else -1
    quantity = trade["quantity"]
    gross = direction * (exit_price - trade["entry"]) * quantity
    exit_fee = exit_price * quantity * fee_rate
    net = gross - trade["entry_fee"] - exit_fee
    balance_after = balance + net
    risk_amount = abs(trade["entry"] - trade["stop"]) * quantity
    row = {
        "Trade ID": trade["id"], "Status": "CLOSED", "Side": trade["side"],
        "Opened UTC": trade["opened_utc"], "Closed UTC": decision.get("candle_utc") or datetime.now(timezone.utc).isoformat(),
        "Entry Price": trade["entry"], "Exit Price": exit_price, "Quantity BTC": f"{quantity:.8f}",
        "Notional USDT": f"{trade['entry'] * quantity:.2f}", "Stop Price": trade["stop"],
        "Target Price": trade["target"], "Entry Fee": f"{trade['entry_fee']:.4f}",
        "Exit Fee": f"{exit_fee:.4f}", "Gross PnL": f"{gross:.4f}", "Net PnL": f"{net:.4f}",
        "R Multiple": f"{net / risk_amount:.4f}", "Exit Reason": reason,
        "Hold Candles": trade["hold_candles"], "Confidence": trade["confidence"],
        "Rationale": trade["rationale"], "Entry Screenshot": trade["screenshot"],
        "Exit Screenshot": str(image_path.resolve()), "Balance After": f"{balance_after:.4f}",
        "Run ID": trade["run_id"],
    }
    return row, balance_after


def signal_row(run_id: str, step: int, captured_utc: str, image_path: Path, decision: dict) -> dict:
    entry = float(decision["entry_price"])
    stop = float(decision["stop_price"])
    target = float(decision["target_price"])
    reward_risk = abs(target - entry) / abs(entry - stop)
    return {
        "Trade ID": f"{run_id}-{step:05d}",
        "Status": "SIGNAL",
        "Side": decision["direction"].upper(),
        "Signal UTC": captured_utc,
        "Entry Price": decision["entry_price"],
        "Stop Price": decision["stop_price"],
        "Target Price": decision["target_price"],
        "Reward Risk": f"{reward_risk:.4f}",
        "Confidence": decision["confidence"],
        "Rationale": decision.get("rationale", ""),
        "Screenshot": str(image_path.resolve()),
        "Run ID": run_id,
        "Step": step,
    }


def build_driver(profile_dir: Path):
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError as exc:
        raise RuntimeError("Selenium is not installed; run: pip install -r trade-image/requirements.txt") from exc

    options = Options()
    options.add_argument(f"--user-data-dir={profile_dir.resolve()}")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    return webdriver.Chrome(options=options)


def run(args) -> int:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.output_dir / run_id
    screenshot_dir = run_dir / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    args.profile_dir.mkdir(parents=True, exist_ok=True)

    driver = build_driver(args.profile_dir)
    try:
        driver.get(args.url)
        print(
            "TradingView opened. On the first run, log in, enable Bar Replay, choose "
            "the starting candle, and leave Forward enabled. Automation will begin automatically.",
            flush=True,
        )
        # Keep this WebElement. TradingView removes the button's title after it
        # displays the first tooltip, although the element itself remains valid.
        forward = wait_for_replay(driver, args.setup_timeout)
        if args.initial_load_wait:
            print(
                f"Replay Forward detected. Waiting {args.initial_load_wait:.0f}s for the initial "
                "chart history to finish loading...",
                flush=True,
            )
            time.sleep(args.initial_load_wait)

        initialize_trade_csv(run_dir / "trades.csv")
        initialize_trade_csv(BASE_DIR / "trades.csv")
        open_trade = None
        balance = args.initial_balance
        step = 0
        closed_trades = 0
        while closed_trades < args.trades:
            if STOP_REQUESTED:
                break
            step += 1
            if not cached_forward_ready(forward):
                forward = wait_for_replay(driver, args.forward_timeout)
            click_forward(driver, forward)
            time.sleep(args.render_wait)

            image_path = screenshot_dir / f"step_{step:05d}.png"
            if not driver.save_screenshot(str(image_path)):
                raise RuntimeError(f"TradingView screenshot failed at step {step}")
            # Keep exactly one rolling chart even when this step's WAIT artifacts
            # are discarded. This also preserves the chart for fatal errors.
            shutil.copyfile(image_path, run_dir / "last_screenshot.png")

            print(f"STEP {step}: screenshot captured; requesting Gemini...", flush=True)
            position_was_open = open_trade is not None
            try:
                decision, _raw = analyze_with_retry(image_path, args, open_trade)
            except Exception as exc:
                if not position_was_open:
                    image_path.unlink(missing_ok=True)
                print(f"STEP {step}: Gemini error: {exc}", file=sys.stderr, flush=True)
                if not args.continue_on_error:
                    return 1
                continue

            record = {
                "step": step,
                "captured_utc": datetime.now(timezone.utc).isoformat(),
                "screenshot": str(image_path),
                "decision": decision,
            }
            keep_artifacts = position_was_open or decision["direction"].upper() in {"LONG", "SHORT"}
            if keep_artifacts:
                save_json(run_dir / f"decision_{step:05d}.json", record)
            else:
                image_path.unlink(missing_ok=True)
                print(f"STEP {step}: no-position WAIT artifacts discarded", flush=True)

            closed_this_candle = False
            if open_trade:
                open_trade["hold_candles"] += 1
                outcome = exit_for_candle(
                    open_trade, float(decision["candle_high"]), float(decision["candle_low"])
                )
                if outcome:
                    exit_price, reason = outcome
                    trade_row, balance = close_trade_row(
                        open_trade, decision, image_path, exit_price, reason, balance, args.fee_rate
                    )
                    append_trade_csv(run_dir / "trades.csv", trade_row)
                    append_trade_csv(BASE_DIR / "trades.csv", trade_row)
                    print(
                        f"STEP {step}: trade {open_trade['id']} closed by {reason} | "
                        f"net PnL={trade_row['Net PnL']} USDT | balance={trade_row['Balance After']}",
                        flush=True,
                    )
                    open_trade = None
                    closed_this_candle = True
                    closed_trades += 1
                    (run_dir / "open_trade.json").unlink(missing_ok=True)
                    print(f"Completed trades: {closed_trades}/{args.trades}", flush=True)

            if not open_trade and not closed_this_candle and decision["direction"].upper() in {"LONG", "SHORT"}:
                row = signal_row(run_id, step, record["captured_utc"], image_path, decision)
                append_signal_csv(run_dir / "signals.csv", row)
                append_signal_csv(BASE_DIR / "signals.csv", row)
                print(f"STEP {step}: signal recorded in {BASE_DIR / 'signals.csv'}", flush=True)
                entry = float(decision["entry_price"])
                stop = float(decision["stop_price"])
                risk_budget = balance * args.risk_percent / 100
                quantity = risk_budget / abs(entry - stop)
                open_trade = {
                    "id": row["Trade ID"], "run_id": run_id, "side": row["Side"],
                    "opened_utc": decision.get("candle_utc") or record["captured_utc"],
                    "entry": entry, "stop": stop, "target": float(decision["target_price"]),
                    "quantity": quantity, "entry_fee": entry * quantity * args.fee_rate,
                    "confidence": decision["confidence"], "rationale": decision.get("rationale", ""),
                    "screenshot": str(image_path.resolve()), "hold_candles": 0,
                }
                save_json(run_dir / "open_trade.json", open_trade)
                print(
                    f"STEP {step}: paper trade opened {open_trade['side']} | qty={quantity:.8f} BTC | "
                    f"risk={risk_budget:.2f} USDT",
                    flush=True,
                )

            print(
                f"STEP {step}: {decision['direction']} | confidence={decision['confidence']} | "
                f"{decision.get('rationale', '')}",
                flush=True,
            )
        if open_trade:
            print(
                f"Run interrupted with trade {open_trade['id']} still open; state remains in "
                f"{run_dir / 'open_trade.json'}",
                flush=True,
            )
        print(f"Run artifacts saved under {run_dir}")
        return 0
    finally:
        if args.keep_browser_open:
            print("Browser left open. Press Ctrl+C here when finished.", flush=True)
            while not STOP_REQUESTED:
                time.sleep(1)
        driver.quit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument(
        "--trades", type=int, default=1,
        help="number of completed paper trades required before stopping (default: 1)",
    )
    parser.add_argument("--render-wait", type=float, default=2.0)
    parser.add_argument("--initial-load-wait", type=float, default=180.0)
    parser.add_argument("--setup-timeout", type=int, default=1800)
    parser.add_argument("--forward-timeout", type=int, default=30)
    parser.add_argument("--gemini-timeout", type=int, default=120)
    parser.add_argument("--gemini-attempts", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=5.0)
    parser.add_argument("--initial-balance", type=float, default=1000.0)
    parser.add_argument("--risk-percent", type=float, default=0.5)
    parser.add_argument("--fee-rate", type=float, default=0.0006)
    parser.add_argument("--model", default=os.getenv("GEMINI_MODEL", "gemini-3.6-flash"))
    parser.add_argument("--context", type=Path, default=DEFAULT_CONTEXT)
    parser.add_argument("--profile-dir", type=Path, default=BASE_DIR / "chrome-profile")
    parser.add_argument("--output-dir", type=Path, default=BASE_DIR / "runs")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--keep-browser-open", action="store_true")
    args = parser.parse_args()
    if (
        args.trades < 1 or args.render_wait < 0 or args.initial_load_wait < 0
        or args.gemini_attempts < 1 or args.retry_delay < 0 or args.initial_balance <= 0
        or args.risk_percent <= 0 or args.fee_rate < 0
    ):
        parser.error("trades/attempts must be positive and wait/delay values cannot be negative")
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
