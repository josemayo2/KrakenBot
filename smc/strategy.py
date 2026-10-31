"""Estrategia SMC: barrido de liquidez -> cambio de estructura -> entrada en FVG.

Setup largo (el corto es simétrico):
  1. Barrido alcista: la mecha rompe un mínimo de liquidez y la vela cierra por encima.
  2. En las `max_bars_after_sweep` velas siguientes, sin hacer un mínimo más bajo que
     el del barrido:
       - (opcional) ruptura alcista de estructura interna (MSS / BOS / CHoCH),
       - aparece un FVG alcista por encima del mínimo del barrido (desplazamiento).
  3. Filtros: tendencia (precio sobre EMA lenta, opcional cruce de EMAs), RSI opcional,
     y distancia del stop entre min_stop_pct y max_stop_pct para que las comisiones
     no se coman el trade.
  4. Orden limitada en el borde proximal del FVG (o en su mitad), stop bajo el
     mínimo del barrido menos un colchón de ATR, objetivo a `rr` veces el riesgo.

Todo se decide al CIERRE de la barra t; el simulador solo puede ejecutar a partir de t+1.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from . import indicators as ind


@dataclass
class SMCParams:
    # liquidez: swings "grandes"
    liq_left: int = 10
    liq_right: int = 3
    max_liquidity_levels: int = 5
    # estructura interna para el MSS
    int_left: int = 3
    int_right: int = 2
    require_mss: bool = True
    # secuencia
    max_bars_after_sweep: int = 15
    fvg_min_atr: float = 0.15
    # entrada / salida
    entry_mode: str = "proximal"   # "proximal" (borde más cercano) | "mid"
    entry_window: int = 10         # velas que la orden limitada sigue viva
    sl_atr_buffer: float = 0.1
    rr: float = 2.0
    min_stop_pct: float = 0.004
    max_stop_pct: float = 0.06
    # filtros
    trend_filter: str = "ema"      # "none" | "ema" (precio vs EMA lenta) | "ema_cross"
    ema_fast: int = 50
    ema_slow: int = 200
    rsi_filter: bool = False
    rsi_len: int = 14
    atr_len: int = 14
    allow_long: bool = True
    allow_short: bool = True


def compute_features(df: pd.DataFrame, p: SMCParams) -> pd.DataFrame:
    f = pd.DataFrame(index=df.index)
    f["atr"] = ind.atr(df, p.atr_len)
    f["ema_fast"] = ind.ema(df["close"], p.ema_fast)
    f["ema_slow"] = ind.ema(df["close"], p.ema_slow)
    f["rsi"] = ind.rsi(df["close"], p.rsi_len)
    f = f.join(ind.market_structure(df, p.int_left, p.int_right))
    f = f.join(ind.liquidity_sweeps(df, p.liq_left, p.liq_right, p.max_liquidity_levels))
    f = f.join(ind.fair_value_gaps(df, f["atr"], p.fvg_min_atr))
    return f


def _trend_ok(side: int, close: float, ef: float, es: float, mode: str) -> bool:
    if mode == "none":
        return True
    if np.isnan(es):
        return False
    ok = close > es if side == 1 else close < es
    if mode == "ema_cross":
        ok = ok and (ef > es if side == 1 else ef < es)
    return ok


def generate_setups(df: pd.DataFrame, p: SMCParams | None = None,
                    features: pd.DataFrame | None = None) -> pd.DataFrame:
    """Devuelve un DataFrame de setups: una fila por orden limitada a colocar."""
    p = p or SMCParams()
    f = features if features is not None else compute_features(df, p)
    o_h, o_l, o_c = df["high"].values, df["low"].values, df["close"].values
    F = {k: f[k].values for k in f.columns}
    n = len(df)
    rows = []
    # estado de cada lado: dict con sweep_bar, extreme, mss
    armed: dict[int, dict | None] = {1: None, -1: None}

    for t in range(n):
        # --- actualizar setups armados con la barra t
        for side in (1, -1):
            a = armed[side]
            if a is None:
                continue
            if t - a["bar"] > p.max_bars_after_sweep:
                armed[side] = None
                continue
            # nuevo extremo más allá del barrido -> invalidado
            if (side == 1 and o_l[t] < a["extreme"]) or (side == -1 and o_h[t] > a["extreme"]):
                armed[side] = None
                continue
            if (side == 1 and F["bull_break"][t]) or (side == -1 and F["bear_break"][t]):
                a["mss"] = True

        # --- nuevos barridos (reemplazan al anterior del mismo lado)
        if p.allow_long and F["bull_sweep"][t]:
            armed[1] = {"bar": t, "extreme": o_l[t], "level": F["bull_sweep_level"][t],
                        "mss": bool(F["bull_break"][t])}
        if p.allow_short and F["bear_sweep"][t]:
            armed[-1] = {"bar": t, "extreme": o_h[t], "level": F["bear_sweep_level"][t],
                         "mss": bool(F["bear_break"][t])}

        # --- ¿FVG que dispara la entrada?
        for side in (1, -1):
            a = armed[side]
            if a is None or t <= a["bar"]:
                continue
            key = "bull" if side == 1 else "bear"
            if not F[f"{key}_fvg"][t]:
                continue
            top, bot = F[f"{key}_fvg_top"][t], F[f"{key}_fvg_bot"][t]
            # el FVG debe quedar del lado correcto del extremo del barrido
            if (side == 1 and bot <= a["extreme"]) or (side == -1 and top >= a["extreme"]):
                continue
            if p.require_mss and not a["mss"]:
                continue
            if not _trend_ok(side, o_c[t], F["ema_fast"][t], F["ema_slow"][t], p.trend_filter):
                continue
            if p.rsi_filter and not ((side == 1 and F["rsi"][t] > 50) or (side == -1 and F["rsi"][t] < 50)):
                continue
            atr_t = F["atr"][t]
            if np.isnan(atr_t):
                continue
            if p.entry_mode == "mid":
                entry = (top + bot) / 2
            else:
                entry = top if side == 1 else bot
            sl = a["extreme"] - side * p.sl_atr_buffer * atr_t
            risk = (entry - sl) * side
            if risk <= 0:
                continue
            stop_pct = risk / entry
            if not (p.min_stop_pct <= stop_pct <= p.max_stop_pct):
                continue
            rows.append({
                "bar": t, "time": df.index[t], "side": side,
                "entry": entry, "sl": sl, "tp": entry + side * p.rr * risk,
                "expiry_bar": t + p.entry_window,
                "sweep_bar": a["bar"], "sweep_level": a["level"],
                "fvg_top": top, "fvg_bot": bot, "stop_pct": stop_pct,
            })
            armed[side] = None  # un setup por barrido

    cols = ["bar", "time", "side", "entry", "sl", "tp", "expiry_bar", "sweep_bar",
            "sweep_level", "fvg_top", "fvg_bot", "stop_pct"]
    return pd.DataFrame(rows, columns=cols)


def params_dict(p: SMCParams) -> dict:
    return asdict(p)
