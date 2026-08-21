import argparse
import csv
import re
from pathlib import Path

import pandas as pd

from replay import run_replay
from settings import Settings


FIVE_MONTHS = {
    "2024-01": "2023-12",
    "2026-04": "2026-03",
    "2026-05": "2026-04",
    "2026-06": "2026-05",
    "2026-07": "2026-06",
}
EIGHTEEN_MONTHS = ["2024-01"] + [
    str(period) for period in pd.period_range("2025-03", "2026-07", freq="M")
]
KNOWN_MONTHS = set(FIVE_MONTHS)


def metrics(month: str, trades_path: Path, sample: str | None = None) -> dict:
    trades = pd.read_csv(trades_path)
    trades = trades.loc[trades.status == "CLOSED"].copy()
    wins = trades.net_pnl > 0
    gains = float(trades.loc[wins, "net_pnl"].sum())
    losses = abs(float(trades.loc[~wins, "net_pnl"].sum()))
    equity = pd.concat([pd.Series([500.0]), trades.balance_after], ignore_index=True)
    return {
        "month": month,
        "sample": sample or (
            "development" if month in {"2024-01", "2026-04", "2026-05"} else "validation"
        ),
        "trades": len(trades),
        "wins": int(wins.sum()),
        "win_rate": float(wins.mean()) if len(trades) else 0.0,
        "targets": int((trades.exit_reason == "TARGET").sum()),
        "stops": int((trades.exit_reason == "STOP").sum()),
        "timeouts": int((trades.exit_reason == "TIMEOUT").sum()),
        "profit_factor": gains / losses if losses else 0.0,
        "net_pnl": float(trades.net_pnl.sum()),
        "fees": float((trades.entry_fee + trades.exit_fee).sum()),
        "max_drawdown": float((equity.cummax() - equity).max()),
    }


def main():
    parser = argparse.ArgumentParser(description="Run the fixed five-month BTC pullback suite")
    parser.add_argument("--version", required=True, help="Folder name, for example pullback_v4")
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--suite", choices=["five", "eighteen"], default="five")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.version):
        raise SystemExit("--version must contain only letters, numbers, underscores, or hyphens")

    config = Settings.from_env()
    version_dir = args.data_root / args.version
    historical = args.data_root / "historical"
    version_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    months = list(FIVE_MONTHS) if args.suite == "five" else EIGHTEEN_MONTHS
    for month in months:
        warmup = str(pd.Period(month, freq="M") - 1)
        output = version_dir / month
        run_replay(
            historical / f"BTCUSDT-5m-{month}.csv",
            output,
            config,
            historical / f"BTCUSDT-5m-{warmup}.csv",
        )
        sample = None if args.suite == "five" else (
            "known" if month in KNOWN_MONTHS else "unseen"
        )
        rows.append(metrics(month, output / "trades.csv", sample))

    summary = version_dir / "summary.csv"
    with summary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(summary)


if __name__ == "__main__":
    main()
