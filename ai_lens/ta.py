"""Funciones técnicas equivalentes a las de Pine Script v6 (ta.*).

Las equivalencias importan: si un EMA o un RMA se inicializa distinto, las puntuaciones
cambian en las primeras velas. Aquí se reproduce el comportamiento de Pine:
  - ta.ema / ta.rma: na hasta tener `length` valores, se siembran con la SMA y luego recursión.
  - Las comparaciones con na son falsas (como en Pine).
"""
from __future__ import annotations

import numpy as np

NaN = np.nan


def _seeded(src: np.ndarray, length: int, alpha: float) -> np.ndarray:
    out = np.full(len(src), NaN)
    valid = np.where(~np.isnan(src))[0]
    if len(valid) < length:
        return out
    start = valid[0]
    # Pine siembra con la SMA de los primeros `length` valores no nulos consecutivos
    seed_end = start + length - 1
    if np.isnan(src[start:seed_end + 1]).any():
        return out
    prev = src[start:seed_end + 1].mean()
    out[seed_end] = prev
    for i in range(seed_end + 1, len(src)):
        x = src[i]
        if np.isnan(x):
            out[i] = prev
            continue
        prev = alpha * x + (1 - alpha) * prev
        out[i] = prev
    return out


def ema(src: np.ndarray, length: int) -> np.ndarray:
    return _seeded(np.asarray(src, float), length, 2.0 / (length + 1))


def rma(src: np.ndarray, length: int) -> np.ndarray:
    return _seeded(np.asarray(src, float), length, 1.0 / length)


def sma(src: np.ndarray, length: int) -> np.ndarray:
    src = np.asarray(src, float)
    out = np.full(len(src), NaN)
    if len(src) >= length:
        c = np.convolve(src, np.ones(length) / length, mode="valid")
        out[length - 1:] = c
    return out


def shift(x: np.ndarray, n: int = 1) -> np.ndarray:
    out = np.full(len(x), NaN)
    if n < len(x):
        out[n:] = x[:-n] if n > 0 else x
    return out


def rsi(close: np.ndarray, length: int) -> np.ndarray:
    ch = close - shift(close)
    up = np.where(np.isnan(ch), NaN, np.maximum(ch, 0))
    dn = np.where(np.isnan(ch), NaN, np.maximum(-ch, 0))
    u, d = rma(up, length), rma(dn, length)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(d == 0, 100.0, np.where(u == 0, 0.0, 100 - 100 / (1 + u / d)))
    r[np.isnan(u) | np.isnan(d)] = NaN
    return r


def macd(close: np.ndarray, fast=12, slow=26, signal=9):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def true_range(high, low, close, handle_na: bool) -> np.ndarray:
    pc = shift(close)
    tr = np.fmax(high - low, np.fmax(np.abs(high - pc), np.abs(low - pc)))
    tr[0] = (high[0] - low[0]) if handle_na else NaN
    return tr


def atr(high, low, close, length: int) -> np.ndarray:
    return rma(true_range(high, low, close, True), length)


def _fixnan(x: np.ndarray) -> np.ndarray:
    out = x.copy()
    last = NaN
    for i in range(len(out)):
        if np.isnan(out[i]):
            out[i] = last
        else:
            last = out[i]
    return out


def dmi(high, low, close, di_len: int, adx_len: int):
    up = high - shift(high)
    down = -(low - shift(low))
    plus_dm = np.where(np.isnan(up), NaN, np.where((up > down) & (up > 0), up, 0.0))
    minus_dm = np.where(np.isnan(down), NaN, np.where((down > up) & (down > 0), down, 0.0))
    trur = rma(true_range(high, low, close, False), di_len)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus = _fixnan(100 * rma(plus_dm, di_len) / trur)
        minus = _fixnan(100 * rma(minus_dm, di_len) / trur)
        s = plus + minus
        adx = 100 * rma(np.abs(plus - minus) / np.where(s == 0, 1, s), adx_len)
    return plus, minus, adx


def percentrank(src: np.ndarray, length: int) -> np.ndarray:
    """% de los `length` valores anteriores que son <= que el actual."""
    out = np.full(len(src), NaN)
    for i in range(length, len(src)):
        window = src[i - length:i]
        if np.isnan(window).any() or np.isnan(src[i]):
            continue
        out[i] = 100.0 * np.sum(window <= src[i]) / length
    return out


def pivots(high: np.ndarray, low: np.ndarray, n: int):
    """ta.pivothigh / ta.pivotlow con n velas a cada lado.

    El valor aparece en la vela i (confirmación) y corresponde a la vela i-n.
    Empates: se exige que el pivote sea estrictamente mayor/menor que sus vecinas.
    """
    ph = np.full(len(high), NaN)
    pl = np.full(len(low), NaN)
    for i in range(2 * n, len(high)):
        c = i - n
        hs = np.concatenate([high[c - n:c], high[c + 1:i + 1]])
        ls = np.concatenate([low[c - n:c], low[c + 1:i + 1]])
        if high[c] > hs.max():
            ph[i] = high[c]
        if low[c] < ls.min():
            pl[i] = low[c]
    return ph, pl


def gt(a, b):
    """a > b con na -> False, como en Pine (NumPy ya devuelve False al comparar NaN)."""
    with np.errstate(invalid="ignore"):
        return np.asarray(a, float) > np.asarray(b, float)


def ge(a, b):
    with np.errstate(invalid="ignore"):
        return np.asarray(a, float) >= np.asarray(b, float)


def lt(a, b):
    return gt(b, a)


def le(a, b):
    return ge(b, a)
