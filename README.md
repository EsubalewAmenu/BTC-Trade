# BTCUSDT Pullback Paper Trader

This project implements one strategy only: a deterministic BTCUSDT trend pullback. It contains
no LLM integration and no exchange order-placement endpoint. It can either watch closed Binance
USD-M futures candles or replay a stored candle file one candle at a time.

## Strategy definition

The default intraday profile uses 15-minute entries, a 1-hour trend filter, and a 4-hour market
context filter.

1. All three timeframes must agree: close above/below EMA 20 and EMA 20 above/below EMA 50.
2. Both EMAs must slope in the same direction over the last three bars.
3. An impulse must close beyond recent swing structure and extend at least `IMPULSE_ATR` from EMA 20.
4. The next 2-6 candles must form a controlled retracement to the EMA/breakout zone without closing
   back through the broken level or EMA 50.
5. A long requires a bullish candle breaking recent pullback highs and closing above EMA 20. A
   short uses the inverse condition.
6. The stop sits beyond the pullback swing with an ATR buffer. Position size includes expected stop
   slippage and both taker fees so configured risk is the total planned loss. The target is adjusted
   when necessary to preserve minimum reward/risk after costs. After a completed candle reaches 1R,
   the stop moves to a cost-adjusted breakeven level for subsequent candles.

Every condition is computed from closed candles. The replay makes a decision after one candle
closes and, if valid, fills at the next candle's open. The entry candle is included in exit
evaluation. If a historical candle touches both stop and target, the replay records STOP first
because tick order is unknown.

## Live paper mode

```bash
cp .env.example .env
# API keys are optional unless REQUIRE_BINANCE_ACCOUNT=true.
docker compose up --build
```

Outputs are `data/events.csv`, `data/trades.csv`, and `data/state.json`. Existing state from the
older version is migrated, but archive the old `data/` directory before comparing a fresh test.

## Historical candle replay

Download a Binance BTCUSDT USD-M futures kline ZIP from:

- https://data.binance.vision/?prefix=data/futures/um/monthly/klines/BTCUSDT/5m/

Or download and extract one official monthly file directly through the container:

```bash
docker compose build
docker compose run --rm btc-paper-trader \
  python download_history.py --month 2024-01 --interval 5m
```

Unzip the CSV under `data/historical/`, then run:

```bash
docker compose build
docker compose run --rm btc-paper-trader \
  python replay.py \
  --input /app/data/historical/BTCUSDT-5m-2024-01.csv \
  --output /app/data/replay-2024-01
```

Replay results appear in:

```text
data/replay-2024-01/events.csv
data/replay-2024-01/trades.csv
data/replay-2024-01/state.json
```

`events.csv` contains one WAIT/LONG/SHORT evaluation for each candle after warmup. `trades.csv`
contains the complete simulated trade lifecycle. Use separate output folders for different test
months so results cannot mix.

Accepted input is either Binance's headerless 12-column kline CSV or a CSV with
`open_time,open,high,low,close,volume` columns. Numeric millisecond and microsecond timestamps and
ISO timestamps are supported.

Use `--warmup-input` with the preceding month's file when testing higher-timeframe filters. Warmup
candles initialize indicators but cannot open trades, keeping the requested month independent.

## Parameters to test

Do not optimize many parameters against the same month. Establish defaults on one development
period and validate them unchanged on later unseen periods.

```text
EMA_FAST / EMA_SLOW
TREND_SLOPE_BARS
MINIMUM_TREND_SEPARATION_ATR
BREAKOUT_LOOKBACK / IMPULSE_ATR
PULLBACK_MIN_BARS / PULLBACK_MAX_BARS
PULLBACK_TOUCH_ATR / PULLBACK_MAX_RETRACE
CONFIRMATION_LOOKBACK / STOP_BUFFER_ATR
REWARD_RISK / MINIMUM_NET_REWARD_RISK / MAX_HOLD_MINUTES
BREAKEVEN_TRIGGER_R
TAKER_FEE_RATE / SLIPPAGE_BPS
```

Before considering live execution, inspect chart screenshots for sampled signals and test at least
100 trades across trending, ranging, high-volatility, and low-volatility periods. A profitable
backtest does not establish that future trading will be profitable.
