# BTCUSDT Intraday Paper Trader

This application reads BTCUSDT USD-M futures data directly from Binance, optionally reads a
real Binance futures account through signed **read-only** requests, asks the selected OpenAI or
Gemini model for a structured `LONG`, `SHORT`, or `WAIT` assessment, and simulates market entries
and exits.

It contains no Binance order-placement endpoint. Every position is paper-only.

## Trading lifecycle

1. Evaluate only closed 5-minute candles with a 15-minute trend filter (temporary scalp profile).
2. Calculate StochRSI, RSI, EMA 20/50, ATR, and relative volume.
3. Request a strict structured LLM decision.
4. Apply deterministic gates: confidence, trend direction, daily loss, trade count, cooldown,
   one position at a time, and maximum 3x notional exposure.
5. Simulate a market fill with configurable slippage and taker fees.
6. Exit at ATR stop, 1.5R target, or one-hour timeout.
7. Save canonical position state to `data/state.json` and the audit ledger to
   `data/paper_trading.xlsx`.

## Credentials

Create a new Binance API key for account reads. Disable withdrawals and trading, and restrict it
to your server's IP where possible. Never paste keys into source files or commit `.env`.

Both OpenAI and Gemini use strict structured JSON output. Market data—not API credentials—is sent
to the selected model. Set `USE_GEMINI=true` for Gemini or `USE_GEMINI=false` for OpenAI. The bot
does not automatically fall back to the other provider: provider errors safely produce `WAIT`.
At startup it makes one small structured request to verify the selected key, model, and API path;
after that, it calls the model only when the deterministic strategy finds a candidate setup.

## Start

```bash
cp .env.example .env
# Edit .env and add the Binance, OpenAI, and Gemini keys. Select USE_GEMINI.
docker compose up --build
```

Workbook and state files appear under `data/`. Stop with `Ctrl+C`. To reset the simulation,
stop the container and move the `data` directory to a backup location before restarting.

## Continuous hosting

For the Google Cloud Always Free allowance, use one standard (not Spot) Compute Engine `e2-micro`
VM in `us-west1`, `us-central1`, or `us-east1`, with at most 30 GB of standard persistent disk.
Keep `data/` on that persistent boot disk, enable VM automatic restart, and start the bot with
Docker Compose. Protect the VM's `.env` with file mode `600`; never store it in Git.

The container writes a heartbeat, retries transient Binance HTTP failures, and exits after
`MAX_CONSECUTIVE_ERRORS` failed cycles so Docker can restart it. It also has a Docker health check,
a 768 MB memory limit, a one-CPU limit, and rotating JSON logs. These measures improve recovery,
but no single free VM can guarantee literally uninterrupted service during every zone or provider
outage.

## Interpretation

The workbook's Dashboard summarizes balance, closed trades, wins, win rate, net PnL, and profit
factor. The Trades sheet holds the complete position lifecycle; Events records every evaluated
candle, including WAIT and risk-gate rejections. Results include estimated taker fees and
slippage but cannot reproduce every real execution condition.

Do not enable real-money order execution based only on a profitable small sample. Review at least
100 paper trades across different market regimes first.
