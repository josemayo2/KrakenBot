"""Carga de datos OHLC: CSV local o descarga desde Kraken.

Kraken Futures (charts API) da histórico largo paginando por tiempo; es la fuente
recomendada para backtest. Kraken spot (/0/public/OHLC) solo devuelve las últimas
720 velas, sirve para comprobar pero no para backtest serio.
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

FUTURES_RESOLUTIONS = {"1m", "5m", "15m", "30m", "1h", "4h", "12h", "1d", "1w"}
SPOT_INTERVALS = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}


def load_csv(path: str | Path) -> pd.DataFrame:
    """Lee un CSV OHLC con cualquiera de las columnas de tiempo usadas en el repo."""
    d = pd.read_csv(path)
    cols = {c.lower(): c for c in d.columns}
    ts = None
    if "timestamp" in cols and pd.api.types.is_numeric_dtype(d[cols["timestamp"]]):
        v = d[cols["timestamp"]]
        ts = pd.to_datetime(v, unit="ms" if v.iloc[0] > 1e11 else "s", utc=True)
    else:
        for name in ("time", "datetime", "date", "timestamp"):
            if name in cols:
                ts = pd.to_datetime(d[cols[name]], utc=True)
                break
    if ts is None:
        raise ValueError(f"No encuentro columna de tiempo en {path}")
    out = pd.DataFrame({k: d[cols[k]].astype(float) for k in ("open", "high", "low", "close")})
    out["volume"] = d[cols["volume"]].astype(float) if "volume" in cols else 0.0
    out.index = ts
    out.index.name = "time"
    out = out[~out.index.duplicated()].sort_index()
    return out


def download_kraken_futures(symbol: str = "PF_XBTUSD", resolution: str = "1h",
                            start: str = "2022-01-01", end: str | None = None,
                            tick_type: str = "trade", pause: float = 0.5) -> pd.DataFrame:
    """Descarga velas de Kraken Futures paginando. Ej.: PF_XBTUSD, PF_ETHUSD, PF_SOLUSD, PF_XRPUSD."""
    import requests

    if resolution not in FUTURES_RESOLUTIONS:
        raise ValueError(f"resolution debe ser una de {sorted(FUTURES_RESOLUTIONS)}")
    url = f"https://futures.kraken.com/api/charts/v1/{tick_type}/{symbol}/{resolution}"
    t0 = int(pd.Timestamp(start, tz="UTC").timestamp())
    t1 = int(pd.Timestamp(end, tz="UTC").timestamp()) if end else int(time.time())
    rows = []
    cur = t0
    while cur < t1:
        r = requests.get(url, params={"from": cur, "to": t1}, timeout=30)
        r.raise_for_status()
        candles = r.json().get("candles", [])
        if not candles:
            break
        rows.extend(candles)
        last = int(candles[-1]["time"]) // 1000
        if last <= cur:
            break
        cur = last + 1
        time.sleep(pause)
    if not rows:
        raise RuntimeError("Kraken no devolvió velas; revisa símbolo/resolución/fechas")
    d = pd.DataFrame(rows)
    out = d[["open", "high", "low", "close", "volume"]].astype(float)
    out.index = pd.to_datetime(d["time"].astype("int64"), unit="ms", utc=True)
    out.index.name = "time"
    return out[~out.index.duplicated()].sort_index()


def load_or_download(csv: str | None = None, symbol: str | None = None, tf: str = "1h",
                     start: str = "2022-01-01", end: str | None = None,
                     cache_dir: str | Path = "data") -> tuple[pd.DataFrame, str]:
    """Carga un CSV o descarga de Kraken Futures con caché en data/. Devuelve (df, etiqueta)."""
    if csv:
        return load_csv(csv), Path(csv).stem
    cache = Path(cache_dir) / f"{symbol}_{tf}_{start}_{end or 'now'}.csv"
    if cache.exists():
        df = load_csv(cache)
    else:
        print(f"Descargando {symbol} {tf} desde {start}...")
        df = download_kraken_futures(symbol, tf, start, end)
        cache.parent.mkdir(exist_ok=True)
        df.reset_index().assign(time=lambda d: d["time"].astype("int64") // 10**6) \
          .rename(columns={"time": "timestamp"}).to_csv(cache, index=False)
    return df, f"{symbol}_{tf}"


def download_kraken_spot(pair: str = "XBTUSD", resolution: str = "1h") -> pd.DataFrame:
    """Últimas 720 velas de Kraken spot (limitación de la API pública)."""
    import requests

    r = requests.get("https://api.kraken.com/0/public/OHLC",
                     params={"pair": pair, "interval": SPOT_INTERVALS[resolution]}, timeout=30)
    r.raise_for_status()
    js = r.json()
    if js.get("error"):
        raise RuntimeError(js["error"])
    key = next(k for k in js["result"] if k != "last")
    d = pd.DataFrame(js["result"][key],
                     columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"])
    out = d[["open", "high", "low", "close", "volume"]].astype(float)
    out.index = pd.to_datetime(d["time"].astype("int64"), unit="s", utc=True)
    out.index.name = "time"
    return out.iloc[:-1]  # la última vela aún no ha cerrado
