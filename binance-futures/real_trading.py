"""Small, guarded Binance USD-M execution layer for BTCUSDT."""

import hashlib
import hmac
import json
import time
from decimal import Decimal, ROUND_DOWN
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class BinanceAPIError(RuntimeError):
    def __init__(self, status, body):
        self.status = status
        self.body = body
        super().__init__(f"Binance HTTP {status}: {body}")


class BinanceFuturesClient:
    def __init__(self, api_key, secret_key, base_url="https://fapi.binance.com", timeout=20):
        if not api_key or not secret_key:
            raise ValueError("BINANCE_API_KEY and BINANCE_SECRET_KEY are required in real mode")
        self.api_key = api_key
        self.secret_key = secret_key.encode()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.time_offset_ms = 0

    def public(self, path, params=None):
        query = urlencode(params or {})
        request = Request(f"{self.base_url}{path}" + (f"?{query}" if query else ""))
        return self._send(request)

    def sync_time(self):
        local = int(time.time() * 1000)
        self.time_offset_ms = int(self.public("/fapi/v1/time")["serverTime"]) - local

    def signed(self, method, path, params=None):
        values = dict(params or {})
        values["timestamp"] = int(time.time() * 1000) + self.time_offset_ms
        values.setdefault("recvWindow", 5000)
        query = urlencode(values)
        signature = hmac.new(self.secret_key, query.encode(), hashlib.sha256).hexdigest()
        encoded = f"{query}&signature={signature}"
        headers = {"X-MBX-APIKEY": self.api_key, "Content-Type": "application/x-www-form-urlencoded"}
        if method == "GET" or method == "DELETE":
            request = Request(f"{self.base_url}{path}?{encoded}", headers=headers, method=method)
        else:
            request = Request(f"{self.base_url}{path}", data=encoded.encode(), headers=headers, method=method)
        return self._send(request)

    def _send(self, request):
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode())
        except HTTPError as exc:
            body = exc.read().decode(errors="replace")
            raise BinanceAPIError(exc.code, body) from exc

    def preflight(self, symbol="BTCUSDT", allow_existing=False):
        self.sync_time()
        if self.signed("GET", "/fapi/v1/positionSide/dual").get("dualSidePosition"):
            raise RuntimeError("Real mode currently requires Binance One-way Position Mode")
        positions = self.signed("GET", "/fapi/v3/positionRisk", {"symbol": symbol})
        if not allow_existing and any(Decimal(p["positionAmt"]) != 0 for p in positions):
            raise RuntimeError(f"Refusing to start: an existing {symbol} position is open")
        regular = self.signed("GET", "/fapi/v1/openOrders", {"symbol": symbol})
        algos = self.signed("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol})
        if not allow_existing and (regular or algos):
            raise RuntimeError(f"Refusing to start: existing {symbol} orders are open")
        balances = self.signed("GET", "/fapi/v3/balance")
        usdt = next((b for b in balances if b["asset"] == "USDT"), None)
        if not usdt:
            raise RuntimeError("No USDT futures balance returned")
        return float(usdt["availableBalance"])

    def symbol_rules(self, symbol="BTCUSDT"):
        info = self.public("/fapi/v1/exchangeInfo")
        item = next(s for s in info["symbols"] if s["symbol"] == symbol)
        filters = {f["filterType"]: f for f in item["filters"]}
        lot = filters.get("MARKET_LOT_SIZE") or filters["LOT_SIZE"]
        return Decimal(lot["stepSize"]), Decimal(lot["minQty"]), Decimal(filters["PRICE_FILTER"]["tickSize"])

    def position_amount(self, symbol="BTCUSDT"):
        rows = self.signed("GET", "/fapi/v3/positionRisk", {"symbol": symbol})
        # Position Information V3 can return an empty list once a symbol is flat.
        return Decimal(rows[0]["positionAmt"]) if rows else Decimal(0)

    def open_algo_orders(self, symbol="BTCUSDT"):
        return self.signed("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol})

    def market_order(self, side, quantity, client_id, reduce_only=False):
        params = {"symbol": "BTCUSDT", "side": side, "type": "MARKET", "quantity": quantity,
                  "newClientOrderId": client_id, "newOrderRespType": "RESULT"}
        if reduce_only:
            params["reduceOnly"] = "true"
        try:
            return self.signed("POST", "/fapi/v1/order", params)
        except BinanceAPIError as exc:
            # A 5xx response has unknown execution status. Reconcile by the unique ID.
            if exc.status < 500:
                raise
            for _ in range(5):
                time.sleep(1)
                try:
                    return self.signed("GET", "/fapi/v1/order", {"symbol": "BTCUSDT", "origClientOrderId": client_id})
                except BinanceAPIError as query_exc:
                    if query_exc.status < 500:
                        raise exc
            raise exc

    def protective_order(self, side, order_type, trigger_price, client_id):
        return self.signed("POST", "/fapi/v1/algoOrder", {
            "algoType": "CONDITIONAL", "symbol": "BTCUSDT", "side": side, "type": order_type,
            "triggerPrice": trigger_price, "workingType": "MARK_PRICE", "closePosition": "true",
            "clientAlgoId": client_id, "newOrderRespType": "RESULT",
        })

    def cancel_algo(self, algo_id):
        try:
            return self.signed("DELETE", "/fapi/v1/algoOrder", {"algoId": algo_id})
        except BinanceAPIError as exc:
            if exc.status == 400:
                return None
            raise

    def query_algo(self, algo_id):
        return self.signed("GET", "/fapi/v1/algoOrder", {"algoId": algo_id})

    def user_trades(self, order_id):
        return self.signed("GET", "/fapi/v1/userTrades", {"symbol": "BTCUSDT", "orderId": order_id})

    def user_trades_since(self, start_time):
        return self.signed("GET", "/fapi/v1/userTrades", {
            "symbol": "BTCUSDT", "startTime": int(start_time), "limit": 1000,
        })


def decimal_floor(value, step):
    value, step = Decimal(str(value)), Decimal(str(step))
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def weighted_fill(trades):
    qty = sum((Decimal(t["qty"]) for t in trades), Decimal(0))
    if not qty:
        raise RuntimeError("Binance returned no fills for the order")
    quote = sum((Decimal(t["price"]) * Decimal(t["qty"]) for t in trades), Decimal(0))
    fee = sum((Decimal(t["commission"]) for t in trades if t["commissionAsset"] == "USDT"), Decimal(0))
    realized = sum((Decimal(t["realizedPnl"]) for t in trades), Decimal(0))
    return float(quote / qty), float(qty), float(fee), float(realized)
