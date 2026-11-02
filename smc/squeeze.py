"""Squeeze Momentum (LazyBear) replicado en Python + reglas de trading propias.

Indicador (idéntico al Pine original):
    BB  = SMA(close, bb_length) ± mult * stdev(close, bb_length)
    KC  = SMA(close, kc_length) ± kc_mult * SMA(TrueRange, kc_length)
    sqz_on  (puntos negros) : BB dentro de KC   -> compresión de volatilidad
    sqz_off (puntos grises) : BB fuera de KC    -> squeeze liberado
    val = linreg(close - avg(avg(highest(high), lowest(low)), sma(close)), kc_length, 0)

    Bug del original: las BB usan multKC (1.5) en vez de mult (2.0). Con
    replicate_bug=True se reproduce tal cual (lo que se ve en TradingView).

Reglas (no están en el indicador, son nuestras):
    Entrada : el squeeze se libera tras >= min_squeeze_bars velas en compresión.
              Largo si val > 0 y sube; corto si val < 0 y baja. Se ejecuta a la
              apertura de la vela siguiente. Opcional: filtro EMA lenta.
    Salida  : exit_mode="color" -> el histograma pierde fuerza (val deja de subir/bajar)
              exit_mode="zero"  -> val cruza cero
              exit_mode="stop_only" -> solo stop / take profit
              Siempre con stop de protección a atr_stop * ATR y TP opcional en R.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import indicators as ind


@dataclass
class SqueezeParams:
    bb_length: int = 20
    bb_mult: float = 2.0
    kc_length: int = 20
    kc_mult: float = 1.5
    use_true_range: bool = True
    replicate_bug: bool = True
    min_squeeze_bars: int = 6
    entry_window: int = 0          # velas extra tras la liberación en las que se acepta entrar
    require_rising: bool = True
    trend_filter: str = "none"     # "none" | "ema"
    ema_slow: int = 200
    exit_mode: str = "color"       # "color" | "zero" | "stop_only"
    atr_len: int = 14
    atr_stop: float = 2.0
    tp_r: float = 0.0              # 0 = sin take profit
    allow_long: bool = True
    allow_short: bool = True


def linreg(y: pd.Series, n: int) -> pd.Series:
    """Equivalente a Pine linreg(y, n, 0): valor de la recta de regresión en la última vela."""
    v = y.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    if len(v) >= n:
        w = np.lib.stride_tricks.sliding_window_view(v, n)
        x = np.arange(n, dtype=float)
        xm = x.mean()
        ym = w.mean(axis=1)
        slope = ((w - ym[:, None]) * (x - xm)).sum(axis=1) / ((x - xm) ** 2).sum()
        out[n - 1:] = ym + slope * (n - 1 - xm)
    return pd.Series(out, index=y.index)


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift()
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()],
                     axis=1).max(axis=1, skipna=False)


def squeeze_indicator(df: pd.DataFrame, p: SqueezeParams) -> pd.DataFrame:
    c, h, lo = df["close"], df["high"], df["low"]
    basis = c.rolling(p.bb_length, min_periods=p.bb_length).mean()
    mult = p.kc_mult if p.replicate_bug else p.bb_mult
    dev = mult * c.rolling(p.bb_length, min_periods=p.bb_length).std(ddof=0)  # Pine stdev = poblacional
    upper_bb, lower_bb = basis + dev, basis - dev

    ma = c.rolling(p.kc_length, min_periods=p.kc_length).mean()
    rng = true_range(df) if p.use_true_range else (h - lo)
    rangema = rng.rolling(p.kc_length, min_periods=p.kc_length).mean()
    upper_kc, lower_kc = ma + rangema * p.kc_mult, ma - rangema * p.kc_mult

    sqz_on = (lower_bb > lower_kc) & (upper_bb < upper_kc)
    sqz_off = (lower_bb < lower_kc) & (upper_bb > upper_kc)

    hh = h.rolling(p.kc_length, min_periods=p.kc_length).max()
    ll = lo.rolling(p.kc_length, min_periods=p.kc_length).min()
    val = linreg(c - ((hh + ll) / 2 + ma) / 2, p.kc_length)

    return pd.DataFrame({"sqz_on": sqz_on, "sqz_off": sqz_off,
                         "no_sqz": ~sqz_on & ~sqz_off, "val": val}, index=df.index)


def generate_squeeze_signals(df: pd.DataFrame, p: SqueezeParams | None = None):
    """Devuelve (entries, exits).

    entries: DataFrame con bar, time, side, stop_dist, tp_r  (decidido al cierre de `bar`)
    exits  : DataFrame booleano exit_long / exit_short (al cierre; se ejecuta en la apertura siguiente)
    """
    p = p or SqueezeParams()
    f = squeeze_indicator(df, p)
    atr_v = ind.atr(df, p.atr_len).to_numpy()
    ema_v = ind.ema(df["close"], p.ema_slow).to_numpy()
    close = df["close"].to_numpy()
    on = f["sqz_on"].to_numpy()
    val = f["val"].to_numpy()
    prev = np.r_[np.nan, val[:-1]]
    n = len(df)

    rows = []
    run = 0                 # velas seguidas en compresión hasta t-1
    release_bar = -10**9    # última liberación válida
    used = True             # ¿ya se usó esa liberación?
    for t in range(n):
        if t > 0 and on[t - 1] and not on[t]:
            if run >= p.min_squeeze_bars:
                release_bar, used = t, False
        run = run + 1 if on[t] else 0

        if used or t - release_bar > p.entry_window or np.isnan(val[t]) or np.isnan(atr_v[t]):
            continue
        rising = val[t] > prev[t] if not np.isnan(prev[t]) else False
        falling = val[t] < prev[t] if not np.isnan(prev[t]) else False
        side = 0
        if p.allow_long and val[t] > 0 and (rising or not p.require_rising):
            side = 1
        elif p.allow_short and val[t] < 0 and (falling or not p.require_rising):
            side = -1
        if side == 0:
            continue
        if p.trend_filter == "ema":
            if np.isnan(ema_v[t]) or (side == 1 and close[t] <= ema_v[t]) or (side == -1 and close[t] >= ema_v[t]):
                continue
        rows.append({"bar": t, "time": df.index[t], "side": side,
                     "stop_dist": p.atr_stop * atr_v[t], "tp_r": p.tp_r})
        used = True

    with np.errstate(invalid="ignore"):
        if p.exit_mode == "color":
            xl, xs = val < prev, val > prev
        elif p.exit_mode == "zero":
            xl, xs = val < 0, val > 0
        elif p.exit_mode == "stop_only":
            xl = xs = np.zeros(n, bool)
        else:
            raise ValueError(f"exit_mode desconocido: {p.exit_mode}")
    exits = pd.DataFrame({"exit_long": xl, "exit_short": xs}, index=df.index)
    entries = pd.DataFrame(rows, columns=["bar", "time", "side", "stop_dist", "tp_r"])
    return entries, exits
