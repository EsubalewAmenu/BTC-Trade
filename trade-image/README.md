# Gemini chart-image experiment

This folder is independent from the existing paper bot. It sends one local chart screenshot and
the pullback field guide to Gemini, prints the validated JSON decision, and saves both parsed and
raw responses under `output/`. It never places or simulates a trade.

The script reads `GEMINI_API_KEY` from the repository root `.env` without printing it.

```bash
python3 trade-image/analyze_chart.py
```

Optional arguments:

```bash
python3 trade-image/analyze_chart.py \
  --image trade-image/screenshot/another-chart.png \
  --model gemini-3.6-flash
```

The default screenshot is `screenshot/BTCUSD_2026-08-25_08-51-32.png`.

## Automated TradingView replay

`replay_runner.py` opens the TradingView chart in a real Chrome window, advances Bar Replay one
candle at a time, captures the visible chart, and sends every screenshot to Gemini. It uses its own
persistent Chrome profile under `trade-image/chrome-profile`, so login normally is required only on
the first run. It does not click Buy/Sell or place trades.

Install Selenium in an isolated environment:

```bash
/usr/bin/python3 -m venv trade-image/.venv
trade-image/.venv/bin/pip install -r trade-image/requirements.txt
```

Run until one paper trade closes:

```bash
trade-image/.venv/bin/python trade-image/replay_runner.py
```

On the first run, log in to TradingView, open **Bar Replay**, and select the starting candle. Leave
the Replay Forward control enabled. The runner detects it and begins automatically; no Enter press
is needed. It waits three minutes after the first detection so TradingView can finish loading its
initial candle history. Decisions and screenshots are grouped in a timestamped folder under
`trade-image/runs/`. Screenshots are retained under each run's `screenshots/` folder. Transient
Gemini 429/5xx failures are retried up to three total attempts with exponential backoff, while also
honoring a longer retry delay returned by Gemini.

To minimize disk usage, a no-position WAIT screenshot is deleted immediately after analysis and no
decision file is written for it. The entry signal screenshot and every screenshot/decision from the
open position through its exit are retained. Raw Gemini responses and the duplicate decisions JSONL
file are not stored.

LONG and SHORT recommendations are appended to the cumulative `trade-image/signals.csv` and to a
run-specific `signals.csv`. No-position WAIT decisions are discarded and are not added to the
signal CSV.

Only one paper position can be open at a time. Future candle high/low observations are checked
against its fixed stop and target; if both are touched inside one replay candle, the stop is counted
first. Completed trades and PnL are written to cumulative `trade-image/trades.csv` and the run's
`trades.csv`. Defaults are 1,000 USDT starting balance, 0.5% account risk per trade, and 0.06% per-side
fees. Override them with `--initial-balance`, `--risk-percent`, and `--fee-rate`.

The runner is limited by completed trades, not replay candles. `--trades 1` stops after one position
reaches its stop or target; `--trades 10` stops after ten completed positions. It continues through
as many WAIT candles as necessary. Ctrl+C stops safely and preserves `open_trade.json`.

Useful options:

```bash
# Stop after 10 paper trades have closed
trade-image/.venv/bin/python trade-image/replay_runner.py --trades 10

# Allow the run to continue if one Gemini request fails
trade-image/.venv/bin/python trade-image/replay_runner.py --trades 10 --continue-on-error
```

Keep the TradingView tab in the foreground while the replay runs. Press Ctrl+C for a clean stop.
