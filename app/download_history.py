import argparse
import re
import urllib.request
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Download official Binance BTCUSDT futures candles")
    parser.add_argument("--month", required=True, help="UTC month in YYYY-MM format")
    parser.add_argument("--interval", default="5m", choices=["1m", "3m", "5m", "15m", "30m", "1h", "4h"])
    parser.add_argument("--output", type=Path, default=Path("/app/data/historical"))
    args = parser.parse_args()
    if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", args.month):
        raise SystemExit("--month must use YYYY-MM")
    name = f"BTCUSDT-{args.interval}-{args.month}"
    url = (
        "https://data.binance.vision/data/futures/um/monthly/klines/"
        f"BTCUSDT/{args.interval}/{name}.zip"
    )
    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / f"{name}.zip"
    target = args.output / f"{name}.csv"
    print(f"Downloading {url}")
    urllib.request.urlretrieve(url, archive)
    with zipfile.ZipFile(archive) as bundle:
        member = bundle.getinfo(f"{name}.csv")
        with bundle.open(member) as source, target.open("wb") as destination:
            while chunk := source.read(1024 * 1024):
                destination.write(chunk)
    print(target)


if __name__ == "__main__":
    main()
