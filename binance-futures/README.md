# Binance Futures live paper trader

This is separate from `trade-image`. It opens the Binance BTCUSDT perpetual page for live visual
context, uses exact public Binance USD-M Futures 15-minute klines for candle/exit prices, and sends
screenshots only to local Qwen/Ollama. It never submits, previews, or clicks a real Binance order.

```bash
/usr/bin/python3 -m venv binance-futures/.venv
binance-futures/.venv/bin/pip install -r binance-futures/requirements.txt
binance-futures/.venv/bin/python binance-futures/live_runner.py
```

There is no fixed login timer. Log in, close dialogs, arrange the chart, and click the `15m`
timeframe when ready. That click starts live scheduling. At every 15-minute candle close, Chrome is
brought to the foreground for 10 seconds so you can see the market, then the screenshot is captured.
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
