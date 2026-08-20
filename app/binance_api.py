import hashlib
import hmac
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import pandas as pd
import requests


class BinanceFuturesClient:
    def __init__(self, base_url: str, timeout: int, api_key: str = "", secret_key: str = ""):
        self.base_url = base_url
        self.timeout = timeout
        self.api_key = api_key
        self.secret_key = secret_key
        self.session = requests.Session()

    def _get(self, path: str, params=None):
        response = self.session.get(
            f"{self.base_url}{path}", params=params or {}, timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()

    def _signed_get(self, path: str, params=None):
        if not self.api_key or not self.secret_key:
            raise ValueError("Binance account credentials are not configured")
        payload = dict(params or {})
        payload.update({"timestamp": int(time.time() * 1000), "recvWindow": 5000})
        query = urlencode(payload)
        payload["signature"] = hmac.new(
            self.secret_key.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        response = self.session.get(
            f"{self.base_url}{path}",
            params=payload,
            headers={"X-MBX-APIKEY": self.api_key},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def klines(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        rows = self._get(
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )
        if not isinstance(rows, list) or not rows:
            raise ValueError("Binance returned no candle data")
        frame = pd.DataFrame(
            rows,
            columns=[
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades", "taker_base",
                "taker_quote", "ignore",
            ],
        )
        numeric = ["open", "high", "low", "close", "volume", "quote_volume"]
        frame[numeric] = frame[numeric].astype(float)
        frame["open_time"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
        frame["close_time"] = pd.to_datetime(frame["close_time"], unit="ms", utc=True)
        now = datetime.now(timezone.utc)
        return frame.loc[frame["close_time"] < now].reset_index(drop=True)

    def mark_price(self, symbol: str) -> float:
        return float(self._get("/fapi/v1/premiumIndex", {"symbol": symbol})["markPrice"])

    def account_summary(self) -> dict:
        account = self._signed_get("/fapi/v3/account")
        usdt = next((a for a in account.get("assets", []) if a["asset"] == "USDT"), {})
        return {
            "can_trade": bool(account.get("canTrade")),
            "total_wallet_balance": float(account.get("totalWalletBalance", 0)),
            "available_balance": float(account.get("availableBalance", usdt.get("availableBalance", 0))),
            "total_unrealized_profit": float(account.get("totalUnrealizedProfit", 0)),
        }

    def send_telegram(self, text: str, token: str, chat_id: str) -> None:
        if not token or not chat_id:
            return
        response = self.session.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=self.timeout,
        )
        response.raise_for_status()
