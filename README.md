# BTCUSDT Intraday Paper Trader

This application reads BTCUSDT USD-M futures data directly from Binance, optionally reads a
real Binance futures account through signed **read-only** requests, asks an OpenAI model for a
structured `LONG`, `SHORT`, or `WAIT` assessment, and simulates market entries and exits.

It contains no Binance order-placement endpoint. Every position is paper-only.

## Trading lifecycle

1. Evaluate only closed 15-minute candles with a 1-hour trend filter.
2. Calculate StochRSI, RSI, EMA 20/50, ATR, and relative volume.
3. Request a strict structured LLM decision.
4. Apply deterministic gates: confidence, trend direction, daily loss, trade count, cooldown,
   one position at a time, and maximum 3x notional exposure.
5. Simulate a market fill with configurable slippage and taker fees.
6. Exit at ATR stop, 1.8R target, or four-hour timeout.
7. Save canonical position state to `data/state.json` and the audit ledger to
   `data/paper_trading.xlsx`.

## Credentials

Create a new Binance API key for account reads. Disable withdrawals and trading, and restrict it
to your server's IP where possible. Never paste keys into source files or commit `.env`.

The OpenAI implementation uses the Responses API with a strict JSON schema. Market data—not API
credentials—is sent to the model.

## Start

```bash
cp .env.example .env
# Edit .env and add the two Binance values and OPENAI_API_KEY.
docker compose up --build
```

Workbook and state files appear under `data/`. Stop with `Ctrl+C`. To reset the simulation,
stop the container and move the `data` directory to a backup location before restarting.

## Interpretation

The workbook's Dashboard summarizes balance, closed trades, wins, win rate, net PnL, and profit
factor. The Trades sheet holds the complete position lifecycle; Events records every evaluated
candle, including WAIT and risk-gate rejections. Results include estimated taker fees and
slippage but cannot reproduce every real execution condition.

Do not enable real-money order execution based only on a profitable small sample. Review at least
100 paper trades across different market regimes first.
