"""Descarga de velas OHLC de Kraken (API pública, sin claves).

Fuentes:
  - futures: perpetuos de Kraken Futures (PF_XBTUSD, PF_ETHUSD, PF_SOLUSD...).
             Historial desde 2022, 2000 velas por petición (se pagina).
  - spot:    Kraken spot (XBTUSD, ETHUSD, SOLUSD). Solo devuelve las últimas 720 velas.

Solo se devuelven velas CERRADAS: la vela en curso se descarta, igual que el indicador
trabaja solo con barstate.isconfirmed.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

TF_SECONDS = {"1h": 3600, "4h": 14400, "12h": 43200, "1d": 86400}
# Código de timeframe de TradingView, para el campo "timeframe" del paquete
TF_TV = {"1h": "60", "4h": "240", "12h": "720", "1d": "D"}

FUTURES_SYMBOLS = {"BTC": "PF_XBTUSD", "ETH": "PF_ETHUSD", "SOL": "PF_SOLUSD"}
SPOT_SYMBOLS = {"BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD"}

CACHE_DIR = Path(__file__).resolve().parent / "cache"


def _get_json(url: str, retries: int = 4) -> dict:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ai-lens-backtest/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # red inestable: reintento con espera creciente
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"No se pudo descargar {url}: {last}")


def _to_ts(x) -> int:
    if isinstance(x, (int, float)):
        return int(x)
    t = pd.Timestamp(x)
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    return int(t.timestamp())


def fetch_futures(symbol: str, tf: str, start, end) -> pd.DataFrame:
    res = tf
    t0, t1 = _to_ts(start), _to_ts(end)
    rows = []
    cur = t0
    while cur < t1:
        q = urllib.parse.urlencode({"from": cur, "to": t1})
        d = _get_json(f"https://futures.kraken.com/api/charts/v1/trade/{symbol}/{res}?{q}")
        candles = d.get("candles", [])
        if not candles:
            break
        rows.extend(candles)
        last = candles[-1]["time"] // 1000
        if not d.get("more_candles") or last + 1 <= cur:
            break
        cur = last + 1
    if not rows:
        raise RuntimeError(f"Sin datos para {symbol} {tf}")
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    return _finish(df, tf)


def fetch_spot(symbol: str, tf: str) -> pd.DataFrame:
    interval = TF_SECONDS[tf] // 60
    d = _get_json(f"https://api.kraken.com/0/public/OHLC?pair={symbol}&interval={interval}")
    if d.get("error"):
        raise RuntimeError(d["error"])
    key = next(k for k in d["result"] if k != "last")
    df = pd.DataFrame(d["result"][key], columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"])
    df["time"] = pd.to_datetime(df["time"].astype(int), unit="s", utc=True)
    return _finish(df[["time", "open", "high", "low", "close", "volume"]], tf)


def _finish(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df = df.drop_duplicates("time").sort_values("time").reset_index(drop=True)
    # descartar la vela que todavía no ha cerrado
    now = datetime.now(timezone.utc)
    closed = df["time"] + pd.Timedelta(seconds=TF_SECONDS[tf]) <= now
    return df[closed].reset_index(drop=True)[["time", "open", "high", "low", "close", "volume"]]


def load(asset: str, tf: str = "4h", source: str = "futures", start="2022-01-01", end=None,
         refresh: bool = False) -> pd.DataFrame:
    """Devuelve las velas cerradas de `asset` (BTC/ETH/SOL o un símbolo de Kraken).

    Guarda una copia en ai_lens/cache/ para no repetir descargas.
    """
    end = pd.Timestamp(end or datetime.now(timezone.utc))
    end = end.tz_localize("UTC") if end.tzinfo is None else end
    sym = (FUTURES_SYMBOLS if source == "futures" else SPOT_SYMBOLS).get(asset.upper(), asset)
    CACHE_DIR.mkdir(exist_ok=True)
    cache = CACHE_DIR / f"{source}_{sym}_{tf}.csv"
    start_ts = pd.Timestamp(start)
    start_ts = start_ts.tz_localize("UTC") if start_ts.tzinfo is None else start_ts
    if cache.exists() and not refresh:
        df = pd.read_csv(cache, parse_dates=["time"])
        df["time"] = pd.to_datetime(df["time"], utc=True)
        if df["time"].iloc[0] <= start_ts:
            fresh = df["time"].iloc[-1] + pd.Timedelta(seconds=2 * TF_SECONDS[tf]) > min(end, pd.Timestamp.now(tz="UTC"))
            if fresh:
                return df
            # actualización incremental: solo las velas nuevas
            new = fetch_futures(sym, tf, df["time"].iloc[-1], end) if source == "futures" else fetch_spot(sym, tf)
            df = (pd.concat([df, new]).drop_duplicates("time", keep="last").sort_values("time")
                  .reset_index(drop=True))
            df.to_csv(cache, index=False)
            return df
    df = fetch_futures(sym, tf, start_ts, end) if source == "futures" else fetch_spot(sym, tf)
    df.to_csv(cache, index=False)
    return df


def ticker(asset: str) -> dict:
    """Precio actual del perpetuo en Kraken Futures (bid, ask, last), API pública."""
    sym = kraken_symbol(asset)
    d = _get_json(f"https://futures.kraken.com/derivatives/api/v3/tickers/{sym}")
    t = d["ticker"]
    return {"bid": float(t["bid"]), "ask": float(t["ask"]), "last": float(t["last"]), "hora": t.get("lastTime")}


def kraken_symbol(asset: str, source: str = "futures") -> str:
    return (FUTURES_SYMBOLS if source == "futures" else SPOT_SYMBOLS).get(asset.upper(), asset)
