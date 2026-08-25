# BTCUSDT Pullback Paper Trader

This project implements one strategy only: a BTCUSDT trend pullback. It supports a guarded LLM
decision mode and contains no exchange order-placement endpoint. It can either watch closed Binance
USD-M futures candles or replay a stored candle file one candle at a time.

## Strategy definition

The default intraday profile uses breakout-and-retest pullbacks on 15-minute entries, a 1-hour
trend filter, and a 4-hour market context filter.

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

In the default `DECISION_MODE=llm`, each newly closed execution candle triggers one request. The
bot downloads enough Binance futures klines to provide exactly 200 closed candles, calculates
EMA50, and posts `system_context` plus CSV `user_context` to `LLM_ENDPOINT_URL`. Invalid HTTP,
non-JSON, stale-candle, low-confidence, non-2R, or invalid price responses become `WAIT`.

The next kline/LLM request is scheduled from `state.json:last_decision_candle`: one complete
`TRADE_INTERVAL` later, plus `CANDLE_CLOSE_DELAY_SECONDS` (20 seconds by default). If state is
behind after a restart, analysis runs immediately. Binance's still-active candle is removed by
comparing its close time with current UTC time; only the final 200 fully closed candles are sent
to the LLM. While a paper position is open, mark price is checked every `POSITION_POLL_SECONDS`
so stop and target monitoring does not wait for the next candle.

The accepted LLM response is:

```json
{
  "direction": "WAIT | LONG | SHORT",
  "entry_price": null,
  "stop_price": null,
  "target_price": null,
  "confidence": 0,
  "rationale": "structural explanation",
  "signal_candle_utc": "optional; informational only"
}
```

For LONG/SHORT, all three prices are required, geometry must be valid, and reward:risk must be at
least 2.0. Trade IDs, quantities, fees, PnL, status, timestamps, exit reasons, and balances are
always calculated locally and never trusted from the LLM.

During development, `LOG_EXTERNAL_RESPONSES=true` logs public Binance response bodies plus the raw
and parsed LLM response at INFO level. Disable it when the integration is stable. Credentials,
signatures, and authenticated request headers are never logged.

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

Run the fixed five-month suite with a version name so every experiment stays contained:

```bash
python evaluate_suite.py --version pullback_v7_breakout_105m
```

Results are stored under `data/<version>/<month>/` with `data/<version>/summary.csv`.

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
