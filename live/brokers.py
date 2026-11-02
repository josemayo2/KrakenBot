"""Brokers: Kraken Futures (real o demo, vía ccxt) y Paper (simulado en local).

Todos exponen la misma interfaz, que es lo único que usa el bot:
    equity() -> float
    position(symbol) -> dict | None        {"side": 1|-1, "size": float, "entry": float}
    market(symbol, side, size, reduce_only, tag, ref_price) -> float (precio medio)
    place_stop(symbol, side, size, stop_price, tag) -> order_id
    cancel(order_id) -> None
    open_order_ids(symbol) -> set[str]
    round_size(symbol, size) -> float ; round_price(symbol, price) -> float ; min_size(symbol) -> float
"""
from __future__ import annotations

import json
import math
from pathlib import Path


class BrokerError(RuntimeError):
    pass


class KrakenFuturesBroker:
    """Kraken Futures (multi-collateral, contratos PF_*). demo=True usa demo-futures.kraken.com."""

    def __init__(self, api_key: str, api_secret: str, demo: bool = False):
        import ccxt

        if not api_key or not api_secret:
            raise BrokerError("Faltan las claves API en el .env")
        self.ex = ccxt.krakenfutures({"apiKey": api_key, "secret": api_secret, "enableRateLimit": True})
        if demo:
            self.ex.set_sandbox_mode(True)
        self.ex.load_markets()

    # ---------------------------------------------------------------- utilidades
    def _market(self, symbol: str) -> dict:
        m = self.ex.markets_by_id.get(symbol) or self.ex.markets_by_id.get(symbol.lower())
        if not m:
            raise BrokerError(f"Símbolo desconocido en Kraken: {symbol}")
        return m[0] if isinstance(m, list) else m

    def round_size(self, symbol: str, size: float) -> float:
        return float(self.ex.amount_to_precision(self._market(symbol)["symbol"], size))

    def round_price(self, symbol: str, price: float) -> float:
        return float(self.ex.price_to_precision(self._market(symbol)["symbol"], price))

    def min_size(self, symbol: str) -> float:
        m = self._market(symbol)
        lim = (m.get("limits") or {}).get("amount", {}).get("min")
        step = (m.get("precision") or {}).get("amount")
        return float(lim or step or 0.0)

    @staticmethod
    def _check(resp: dict, key: str) -> dict:
        st = resp.get(key) or {}
        if resp.get("result") != "success" or st.get("status") not in ("placed", "cancelled"):
            raise BrokerError(f"Kraken rechazó la orden: {json.dumps(resp)[:500]}")
        return st

    # ---------------------------------------------------------------- cuenta
    def equity(self) -> float:
        acc = self.ex.privateGetAccounts().get("accounts", {})
        flex = acc.get("flex") or {}
        for k in ("marginEquity", "portfolioValue", "balanceValue"):
            if flex.get(k) is not None:
                return float(flex[k])
        raise BrokerError("No encuentro el saldo de la cuenta multi-collateral (flex)")

    def position(self, symbol: str):
        for p in self.ex.privateGetOpenpositions().get("openPositions", []):
            if p.get("symbol", "").upper() == symbol.upper() and float(p.get("size", 0)) > 0:
                side = 1 if p.get("side") == "long" else -1
                entry = float(p.get("price") or p.get("entryPrice") or 0)
                return {"side": side, "size": float(p["size"]), "entry": entry}
        return None

    def open_order_ids(self, symbol: str) -> set[str]:
        orders = self.ex.privateGetOpenorders().get("openOrders", [])
        return {o["order_id"] for o in orders if o.get("symbol", "").upper() == symbol.upper()}

    # ---------------------------------------------------------------- órdenes
    def market(self, symbol, side, size, reduce_only=False, tag="", ref_price=None) -> float:
        req = {"orderType": "mkt", "symbol": symbol, "side": "buy" if side == 1 else "sell",
               "size": self.ex.amount_to_precision(self._market(symbol)["symbol"], size)}
        if reduce_only:
            req["reduceOnly"] = "true"
        if tag:
            req["cliOrdId"] = tag[:100]
        st = self._check(self.ex.privatePostSendorder(req), "sendStatus")
        execs = [e for e in st.get("orderEvents", []) if e.get("type") == "EXECUTION"]
        qty = sum(float(e.get("amount", 0)) for e in execs)
        if qty > 0:
            return sum(float(e["price"]) * float(e.get("amount", 0)) for e in execs) / qty
        if ref_price is None:
            raise BrokerError("Orden enviada pero sin ejecución confirmada")
        return float(ref_price)

    def place_stop(self, symbol, side, size, stop_price, tag="") -> str:
        """Stop-market reduce-only. `side` es el lado de la ORDEN (contrario a la posición)."""
        m = self._market(symbol)["symbol"]
        req = {"orderType": "stp", "symbol": symbol, "side": "buy" if side == 1 else "sell",
               "size": self.ex.amount_to_precision(m, size),
               "stopPrice": self.ex.price_to_precision(m, stop_price),
               "triggerSignal": "last", "reduceOnly": "true"}
        if tag:
            req["cliOrdId"] = tag[:100]
        st = self._check(self.ex.privatePostSendorder(req), "sendStatus")
        return st["order_id"]

    def cancel(self, order_id: str) -> None:
        r = self.ex.privatePostCancelorder({"order_id": order_id})
        status = (r.get("cancelStatus") or {}).get("status")
        if status not in ("cancelled", "notFound", "filled"):
            raise BrokerError(f"No se pudo cancelar {order_id}: {json.dumps(r)[:300]}")


class PaperBroker:
    """Simulación local persistente. Ejecuta a precio de referencia + slippage y cobra comisión taker."""

    def __init__(self, path: str, initial_equity: float = 1000.0, fee: float = 0.0005, slippage: float = 0.0002):
        self.path = Path(path)
        self.fee, self.slip = fee, slippage
        if self.path.exists():
            self.s = json.loads(self.path.read_text())
        else:
            self.s = {"cash": initial_equity, "positions": {}, "stops": {}, "next_id": 1}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.s, indent=2))

    def round_size(self, symbol, size):
        return math.floor(size * 1e6) / 1e6

    def round_price(self, symbol, price):
        return round(price, 8)

    def min_size(self, symbol):
        return 1e-6

    def equity(self, marks: dict | None = None) -> float:
        eq = self.s["cash"]
        for sym, p in self.s["positions"].items():
            px = (marks or {}).get(sym, p["entry"])
            eq += p["side"] * p["size"] * (px - p["entry"])
        return eq

    def position(self, symbol):
        p = self.s["positions"].get(symbol)
        return dict(p) if p else None

    def open_order_ids(self, symbol):
        return {oid for oid, o in self.s["stops"].items() if o["symbol"] == symbol}

    def _fill(self, symbol, side, size, price):
        self.s["cash"] -= size * price * self.fee
        p = self.s["positions"].get(symbol)
        if p is None:
            self.s["positions"][symbol] = {"side": side, "size": size, "entry": price}
        else:  # cierre (el bot solo usa reduce-only para cerrar entero)
            self.s["cash"] += p["side"] * p["size"] * (price - p["entry"])
            del self.s["positions"][symbol]
        self._save()

    def market(self, symbol, side, size, reduce_only=False, tag="", ref_price=None):
        if ref_price is None:
            raise BrokerError("PaperBroker necesita ref_price")
        price = ref_price * (1 + side * self.slip)
        self._fill(symbol, side, size, price)
        return price

    def place_stop(self, symbol, side, size, stop_price, tag=""):
        oid = f"paper-{self.s['next_id']}"
        self.s["next_id"] += 1
        self.s["stops"][oid] = {"symbol": symbol, "side": side, "size": size, "stop": stop_price}
        self._save()
        return oid

    def cancel(self, order_id):
        self.s["stops"].pop(order_id, None)
        self._save()

    def simulate_stops(self, symbol, candles_since_entry) -> float | None:
        """Comprueba si alguna vela cerrada tocó el stop. Devuelve precio de salida o None."""
        for oid, o in list(self.s["stops"].items()):
            if o["symbol"] != symbol or symbol not in self.s["positions"]:
                continue
            for _, c in candles_since_entry.iterrows():
                hit = (o["side"] == -1 and c["low"] <= o["stop"]) or (o["side"] == 1 and c["high"] >= o["stop"])
                if hit:
                    gap = (o["side"] == -1 and c["open"] <= o["stop"]) or (o["side"] == 1 and c["open"] >= o["stop"])
                    px = (c["open"] if gap else o["stop"]) * (1 + o["side"] * self.slip)
                    del self.s["stops"][oid]
                    self._fill(symbol, o["side"], o["size"], px)
                    return px
        return None
