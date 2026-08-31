# Binance Futures paper/real trader

This is separate from `trade-image`. It opens the Binance BTCUSDT perpetual page for live visual
context, uses exact public Binance USD-M Futures 15-minute klines for candle/exit prices, and sends
screenshots to Gemini using `GEMINI_API_KEY` from the root `.env`. The detailed structure-strategy prompt
is `binance-futures/gemini_system_context.txt`. Paper mode remains the default. Real mode is explicit,
hard-capped, and uses signed Binance USD-M API orders rather than browser clicks.

```bash
/usr/bin/python3 -m venv binance-futures/.venv
binance-futures/.venv/bin/pip install -r binance-futures/requirements.txt
binance-futures/.venv/bin/python binance-futures/live_runner.py
```

There is no fixed login timer. Log in, close dialogs, arrange the chart, and click the `15m`
timeframe when ready. That click starts live scheduling. Chrome is brought forward 20 seconds before
each 15-minute close, remains visible for 10 seconds, and captures the screenshot 10 seconds before
the close. After the boundary, the runner fetches the finalized Binance candle and starts analysis.
The runner stops after one completed paper trade by default. Use `--trades 10` for ten trades.

Paper execution defaults:

- Starting balance: 1,000 USDT
- Risk: 0.5% of current balance per trade
- Leverage/margin cap: 1x
- Taker fee: 0.05% per side (configurable; verify against your actual Binance tier)
- Adverse slippage: 1 basis point per fill
- BTC quantity step: 0.001
- Conservative stop-first result if stop and target are both crossed inside one candle

Example custom run:

```bash
binance-futures/.venv/bin/python binance-futures/live_runner.py \
  --trades 5 --initial-balance 500 --risk-percent 0.5 --leverage 2 \
  --taker-fee 0.0005 --slippage-bps 1
```

All runs share exactly one `binance-futures/signals.csv`, one `binance-futures/trades.csv`, and one
`binance-futures/screenshots/` folder. Only entry/open-position/exit screenshots and decisions are
retained, plus one rolling `last_screenshot.png`; no new run-ID directories are created.

## Guarded real mode

The `.env` file must contain `BINANCE_API_KEY` and `BINANCE_SECRET_KEY`. The key needs USD-M Futures
read and trading permission, and its IP restriction must allow the current public IP. Real mode
requires One-way Position Mode and refuses to start if a BTCUSDT position or order already exists.

First run the read-only check (it cannot place an order):

```bash
binance-futures/.venv/bin/python binance-futures/live_runner.py \
  --mode real --confirm-real-trading I_UNDERSTAND --preflight-only
```

Then run one real trade with a desired/hard-capped notional of 100 USDT:

```bash
binance-futures/.venv/bin/python binance-futures/live_runner.py \
  --mode real --trades 1 --real-notional 100 --max-real-notional 100 \
  --confirm-real-trading I_UNDERSTAND
```

BTC quantity is rounded down to Binance's current market step, so the actual notional can be below
100 USDT. After the market entry fills, close-all stop-loss and take-profit orders are submitted
immediately. While the position is open, Gemini and screenshot scheduling pause; the runner checks the
signed Binance position endpoint once after each 15-minute candle closes. If the runner restarts,
it resumes the real trade recorded in `open_trade.json` instead of refusing the existing position.
On closure it reads the actual fills,
commission, realized PnL, and records them in `trades.csv`. Ctrl-C leaves confirmed protective
orders active on Binance.
