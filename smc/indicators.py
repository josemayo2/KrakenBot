"""Indicadores SMC causales: el valor en la barra t solo usa datos hasta el cierre de t.

Regla clave anti-repintado: un pivote (swing high/low) en la barra i solo se
"conoce" en la barra i + right, cuando ya han cerrado las `right` velas que lo
confirman. Todo lo que dependa de swings usa esa barra de confirmación.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------- clásicos
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift()
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


# ---------------------------------------------------------------- pivotes
def pivot_points(high, low, left: int, right: int):
    """Pivotes confirmados.

    Devuelve (ph, pl): arrays de longitud n donde ph[t] es el precio del swing high
    confirmado EN la barra t (pivote real en t-right), NaN si no hay ninguno.
    Swing high: high[i] > máximo de las `left` velas previas y >= máximo de las
    `right` siguientes.
    """
    if left < 1 or right < 1:
        raise ValueError("left y right deben ser >= 1")
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    n = len(h)
    ph = np.full(n, np.nan)
    pl = np.full(n, np.nan)
    for i in range(left, n - right):
        if h[i] > h[i - left:i].max() and h[i] >= h[i + 1:i + right + 1].max():
            ph[i + right] = h[i]
        if lo[i] < lo[i - left:i].min() and lo[i] <= lo[i + 1:i + right + 1].min():
            pl[i + right] = lo[i]
    return ph, pl


# ---------------------------------------------------------------- estructura
def market_structure(df: pd.DataFrame, left: int, right: int) -> pd.DataFrame:
    """BOS / CHoCH por cierre más allá del último swing confirmado y no roto.

    Columnas:
        bull_break / bear_break : ruptura alcista / bajista en esta barra
        choch                   : la ruptura cambia la tendencia previa (CHoCH);
                                  si no, es BOS
        trend                   : 1 alcista, -1 bajista, 0 indefinida
    """
    ph, pl = pivot_points(df["high"].values, df["low"].values, left, right)
    close = df["close"].values
    n = len(close)
    bull = np.zeros(n, bool)
    bear = np.zeros(n, bool)
    choch = np.zeros(n, bool)
    trend_arr = np.zeros(n, int)
    sh = sl = np.nan
    sh_broken = sl_broken = True
    trend = 0
    for t in range(n):
        if not np.isnan(ph[t]):
            sh, sh_broken = ph[t], False
        if not np.isnan(pl[t]):
            sl, sl_broken = pl[t], False
        if not sh_broken and close[t] > sh:
            bull[t] = True
            choch[t] = trend == -1
            trend, sh_broken = 1, True
        elif not sl_broken and close[t] < sl:
            bear[t] = True
            choch[t] = trend == 1
            trend, sl_broken = -1, True
        trend_arr[t] = trend
    return pd.DataFrame(
        {"bull_break": bull, "bear_break": bear, "choch": choch, "trend": trend_arr},
        index=df.index,
    )


# ---------------------------------------------------------------- liquidez
def liquidity_sweeps(df: pd.DataFrame, left: int, right: int, max_levels: int = 5) -> pd.DataFrame:
    """Barridos de liquidez sobre swings confirmados no tocados (pools de liquidez).

    Barrido alcista (sell-side): la mecha atraviesa uno o varios mínimos previos
    y la vela CIERRA por encima de todos ellos. Si cierra por debajo es una
    ruptura, no un barrido. En ambos casos esos niveles dejan de estar activos.
    """
    ph, pl = pivot_points(df["high"].values, df["low"].values, left, right)
    h, lo, c = df["high"].values, df["low"].values, df["close"].values
    n = len(c)
    bull = np.zeros(n, bool)
    bear = np.zeros(n, bool)
    bull_level = np.full(n, np.nan)
    bear_level = np.full(n, np.nan)
    highs: list[float] = []
    lows: list[float] = []
    for t in range(n):
        if not np.isnan(ph[t]):
            highs = (highs + [ph[t]])[-max_levels:]
        if not np.isnan(pl[t]):
            lows = (lows + [pl[t]])[-max_levels:]

        swept = [lv for lv in lows if lo[t] < lv]
        if swept:
            if c[t] > max(swept):
                bull[t] = True
                bull_level[t] = max(swept)
            lows = [lv for lv in lows if lv not in swept]

        swept = [lv for lv in highs if h[t] > lv]
        if swept:
            if c[t] < min(swept):
                bear[t] = True
                bear_level[t] = min(swept)
            highs = [lv for lv in highs if lv not in swept]

    return pd.DataFrame(
        {"bull_sweep": bull, "bull_sweep_level": bull_level,
         "bear_sweep": bear, "bear_sweep_level": bear_level},
        index=df.index,
    )


# ---------------------------------------------------------------- FVG
def fair_value_gaps(df: pd.DataFrame, atr_s: pd.Series, min_atr: float = 0.0) -> pd.DataFrame:
    """FVG de 3 velas, conocido al cierre de la tercera (barra t).

    Alcista: low[t] > high[t-2]  -> zona [high[t-2], low[t]]
    Bajista: high[t] < low[t-2]  -> zona [high[t], low[t-2]]
    Se exige un tamaño mínimo de `min_atr` * ATR.
    """
    h2 = df["high"].shift(2)
    l2 = df["low"].shift(2)
    bull = (df["low"] > h2) & ((df["low"] - h2) >= min_atr * atr_s)
    bear = (df["high"] < l2) & ((l2 - df["high"]) >= min_atr * atr_s)
    return pd.DataFrame(
        {
            "bull_fvg": bull.fillna(False).astype(bool),
            "bull_fvg_top": df["low"].where(bull),
            "bull_fvg_bot": h2.where(bull),
            "bear_fvg": bear.fillna(False).astype(bool),
            "bear_fvg_top": l2.where(bear),
            "bear_fvg_bot": df["high"].where(bear),
        },
        index=df.index,
    )
