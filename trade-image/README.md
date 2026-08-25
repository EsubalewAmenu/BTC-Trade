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
