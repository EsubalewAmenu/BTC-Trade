#!/usr/bin/env python3
"""Advance a TradingView bar replay and analyze each rendered chart with Gemini."""

import argparse
import json
import os
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

        decisions_path = run_dir / "decisions.jsonl"
        for step in range(1, args.steps + 1):
            if STOP_REQUESTED:
                break
            if not cached_forward_ready(forward):
                forward = wait_for_replay(driver, args.forward_timeout)
            click_forward(driver, forward)
            time.sleep(args.render_wait)

            image_path = screenshot_dir / f"step_{step:05d}.png"
            if not driver.save_screenshot(str(image_path)):
                raise RuntimeError(f"TradingView screenshot failed at step {step}")

            print(f"STEP {step}: screenshot saved; requesting Gemini...", flush=True)
            try:
                decision, raw = analyze(image_path, args.context, args.model, args.gemini_timeout)
            except Exception as exc:
                error = {"step": step, "screenshot": str(image_path), "error": str(exc)}
                with decisions_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(error) + "\n")
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
            save_json(run_dir / f"decision_{step:05d}.json", record)
            save_json(run_dir / f"raw_{step:05d}.json", raw)
            with decisions_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record) + "\n")

            print(
                f"STEP {step}: {decision['direction']} | confidence={decision['confidence']} | "
                f"{decision.get('rationale', '')}",
                flush=True,
            )
            if args.stop_on_signal and decision["direction"].upper() != "WAIT":
                print("Stopping because Gemini produced a trade signal.", flush=True)
                break
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
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--render-wait", type=float, default=2.0)
    parser.add_argument("--setup-timeout", type=int, default=1800)
    parser.add_argument("--forward-timeout", type=int, default=30)
    parser.add_argument("--gemini-timeout", type=int, default=120)
    parser.add_argument("--model", default=os.getenv("GEMINI_MODEL", "gemini-3.6-flash"))
    parser.add_argument("--context", type=Path, default=DEFAULT_CONTEXT)
    parser.add_argument("--profile-dir", type=Path, default=BASE_DIR / "chrome-profile")
    parser.add_argument("--output-dir", type=Path, default=BASE_DIR / "runs")
    parser.add_argument("--stop-on-signal", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--keep-browser-open", action="store_true")
    args = parser.parse_args()
    if args.steps < 1 or args.render_wait < 0:
        parser.error("--steps must be positive and --render-wait cannot be negative")
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
