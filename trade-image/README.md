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

Start a 50-candle test:

```bash
trade-image/.venv/bin/python trade-image/replay_runner.py --steps 50
```

On the first run, log in to TradingView, open **Bar Replay**, and select the starting candle. Leave
the Replay Forward control enabled. The runner detects it and begins automatically; no Enter press
is needed. Results, raw Gemini responses, and screenshots are grouped in a timestamped folder under
`trade-image/runs/`.

Useful options:

```bash
# Stop at the first LONG or SHORT decision
trade-image/.venv/bin/python trade-image/replay_runner.py --steps 100 --stop-on-signal

# Allow the run to continue if one Gemini request fails
trade-image/.venv/bin/python trade-image/replay_runner.py --steps 100 --continue-on-error
```

Keep the TradingView tab in the foreground while the replay runs. Press Ctrl+C for a clean stop.
