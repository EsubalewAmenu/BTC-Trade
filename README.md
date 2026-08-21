# BTCUSDT Pullback Paper Trader

This project implements one strategy only: a deterministic BTCUSDT trend pullback. It contains
no LLM integration and no exchange order-placement endpoint. It can either watch closed Binance
USD-M futures candles or replay a stored candle file one candle at a time.

## Strategy definition

The default scalp profile uses 5-minute entries and a 15-minute trend filter.

1. Both timeframes must agree: close above/below EMA 20 and EMA 20 above/below EMA 50.
2. Both EMAs must slope in the same direction over the last three bars.
3. A prior impulse must extend at least `IMPULSE_ATR` beyond EMA 20.
4. The next 2-6 candles must retrace to the EMA 20 zone without closing through EMA 50.
5. A long requires a bullish candle closing above the previous high and EMA 20. A short uses the
   inverse condition.
6. Risk is sized from ATR, limited by account risk and maximum notional exposure. Exits use an ATR
   stop, fixed R target, maximum holding time, fees, and adverse slippage.

Every condition is computed from closed candles. The replay makes a decision after one candle
closes and, if valid, fills at the next candle's open. If a historical candle touches both stop
and target, the replay records STOP first because tick order is unknown.

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

## Parameters to test

Do not optimize many parameters against the same month. Establish defaults on one development
period and validate them unchanged on later unseen periods.

```text
EMA_FAST / EMA_SLOW
TREND_SLOPE_BARS
IMPULSE_LOOKBACK / IMPULSE_ATR
PULLBACK_MIN_BARS / PULLBACK_MAX_BARS
PULLBACK_TOUCH_ATR
STOP_ATR / REWARD_RISK / MAX_HOLD_MINUTES
TAKER_FEE_RATE / SLIPPAGE_BPS
```

Before considering live execution, inspect chart screenshots for sampled signals and test at least
100 trades across trending, ranging, high-volatility, and low-volatility periods. A profitable
backtest does not establish that future trading will be profitable.
