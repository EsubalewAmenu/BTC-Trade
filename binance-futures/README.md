# Binance Futures live paper trader

This is separate from `trade-image`. It opens the Binance BTCUSDT perpetual page for live visual
context, uses exact public Binance USD-M Futures 15-minute klines for candle/exit prices, and sends
screenshots only to local Qwen/Ollama. It never submits, previews, or clicks a real Binance order.

```bash
/usr/bin/python3 -m venv binance-futures/.venv
binance-futures/.venv/bin/pip install -r binance-futures/requirements.txt
binance-futures/.venv/bin/python binance-futures/live_runner.py
```

The browser stays visible for the first 120 seconds so you can log in, close dialogs, and arrange the
chart. The runner attempts to select `15m`, then evaluates only at candle close + 20 seconds. It
stops after one completed paper trade by default. Use `--trades 10` for ten completed trades.

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

Completed trades are appended to `binance-futures/trades.csv` and the timestamped run's
`trades.csv`. Only entry/open-position/exit screenshots and decisions are retained, plus one rolling
`last_screenshot.png`.
