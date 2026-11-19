"""AI Agent Lens en Python: port fiel de ai-agent-lens.pine (MIT, roan-maverick).

Original: https://github.com/roan-maverick/tradingview-indicator-for-ai-agents

Para cada vela cerrada calcula lo mismo que el indicador de TradingView:
puntuaciones LONG/SHORT (0-100, cinco factores de 20), régimen, estructura,
anatomía de la vela, setups, seguimiento de la operación (entrada, stop ATR,
objetivos 1,5R y 3R) y el paquete JSON "ai-agent-lens/v1".

Uso:
    from ai_lens import data, indicator
    df = data.load("BTC", "4h")
    lens = indicator.compute(df, tf="4h")
    paquete = indicator.packet(lens, i=len(lens) - 1)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import ta
from .ta import NaN, ge, gt, le, lt

SCHEMA = "ai-agent-lens/v1"

TF_CODE_SECONDS = {"1": 60, "5": 300, "15": 900, "30": 1800, "60": 3600, "120": 7200, "240": 14400,
                   "720": 43200, "D": 86400, "1D": 86400, "W": 604800, "1W": 604800}
TF_ALIASES = {"1h": "60", "2h": "120", "4h": "240", "12h": "720", "1d": "D", "1w": "W"}


def tf_code(tf: str) -> str:
    return TF_ALIASES.get(tf, tf)


@dataclass
class Params:
    """Valores por defecto = los del indicador en TradingView."""
    score_threshold: int = 70
    block_high_vol: bool = True
    htf: str = "240"
    htf_ema_len: int = 50
    ema_fast: int = 20
    ema_mid: int = 50
    ema_slow: int = 200
    slope_len: int = 5
    rsi_len: int = 14
    rsi_ob: float = 75
    rsi_os: float = 25
    adx_len: int = 14
    adx_trend: float = 20
    vol_len: int = 20
    atr_len: int = 14
    atr_pct_len: int = 100
    high_vol_pct: float = 90
    swing_len: int = 5
    stop_atr: float = 1.5
    t1_r: float = 1.5
    t2_r: float = 3.0
    recent_count: int = 5


@dataclass
class Lens:
    """Resultado del indicador: una fila por vela cerrada + operaciones seguidas."""
    df: pd.DataFrame
    trades: list = field(default_factory=list)
    params: Params = field(default_factory=Params)
    tf: str = "240"
    symbol: str = ""
    price_decimals: int = 2


# ─────────────────────────────────────────────────────────────────────────────
# Datos de marco temporal superior (request.security con [1] + lookahead_on)
# ─────────────────────────────────────────────────────────────────────────────
def _period_start(t: pd.Series, sec: int) -> np.ndarray:
    epoch = (pd.to_datetime(t, utc=True) - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)
    epoch = np.asarray(epoch, dtype="int64")
    if sec == 604800:  # semanas que empiezan en lunes (01-01-1970 fue jueves)
        return ((epoch - 4 * 86400) // sec) * sec + 4 * 86400
    return (epoch // sec) * sec


def _htf_prev(df: pd.DataFrame, sec: int, ema_len: int | None = None) -> pd.DataFrame:
    """Valores de la vela anterior del marco superior que contiene cada vela del gráfico."""
    p = _period_start(df["time"], sec)
    g = df.assign(_p=p).groupby("_p", sort=True)
    h = pd.DataFrame({"high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last()})
    if ema_len:
        h["ema"] = ta.ema(h["close"].to_numpy(), ema_len)
    prev = h.shift(1)
    return prev.reindex(p).reset_index(drop=True)


def _vwap_daily(df: pd.DataFrame) -> np.ndarray:
    hlc3 = (df["high"] + df["low"] + df["close"]) / 3
    day = _period_start(df["time"], 86400)
    pv = (hlc3 * df["volume"]).groupby(day).cumsum()
    v = df["volume"].groupby(day).cumsum()
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.array(pv / v, dtype=float)
    out[~np.isfinite(out)] = NaN
    return out


def _pattern(o, h, l, c, po, pc, ph_, pl_, a) -> str:
    rng = h - l
    body = abs(c - o)
    up = h - max(o, c)
    dn = min(o, c) - l
    if rng <= 0:
        return "FLAT"
    if c > o and pc < po and c >= po and o <= pc:
        return "BULLISH_ENGULFING"
    if c < o and pc > po and o >= pc and c <= po:
        return "BEARISH_ENGULFING"
    if body / rng <= 0.1:
        return "DOJI"
    if dn >= 2 * body and up <= 0.15 * rng:
        return "HAMMER"
    if up >= 2 * body and dn <= 0.15 * rng:
        return "SHOOTING_STAR"
    if body / rng >= 0.9:
        return "BULLISH_MARUBOZU" if c > o else "BEARISH_MARUBOZU"
    if h < ph_ and l > pl_:
        return "INSIDE_BAR"
    if h > ph_ and l < pl_:
        return "OUTSIDE_BAR"
    if a > 0 and rng >= 1.8 * a:
        return "WIDE_RANGE_BULL" if c > o else "WIDE_RANGE_BEAR"
    return "NONE"


def _decimals(x: np.ndarray) -> int:
    sample = x[~np.isnan(x)][-500:]
    best = 0
    for v in sample:
        for d in range(0, 9):
            if abs(round(v, d) - v) < 1e-9:
                best = max(best, d)
                break
    return best


# ─────────────────────────────────────────────────────────────────────────────
# Cálculo principal
# ─────────────────────────────────────────────────────────────────────────────
def compute(df: pd.DataFrame, tf: str = "4h", params: Params | None = None, symbol: str = "") -> Lens:
    P = params or Params()
    tfc = tf_code(tf)
    df = df.reset_index(drop=True).copy()
    o, h, l, c, v = (df[k].to_numpy(float) for k in ["open", "high", "low", "close", "volume"])
    n = len(df)

    emaF, emaM, emaS = ta.ema(c, P.ema_fast), ta.ema(c, P.ema_mid), ta.ema(c, P.ema_slow)
    rsiV = ta.rsi(c, P.rsi_len)
    _, _, hist = ta.macd(c, 12, 26, 9)
    diP, diM, adxV = ta.dmi(h, l, c, P.adx_len, P.adx_len)
    atrV = ta.atr(h, l, c, P.atr_len)
    atrPct = ta.percentrank(atrV, P.atr_pct_len)
    volAvg = ta.sma(v, P.vol_len)
    with np.errstate(divide="ignore", invalid="ignore"):
        relVol = np.where(volAvg > 0, v / volAvg, NaN)
    vwapV = _vwap_daily(df)

    daily = _htf_prev(df, 86400)
    pdh, pdl = daily["high"].to_numpy(), daily["low"].to_numpy()

    chart_sec = TF_CODE_SECONDS[tfc]
    htf_sec = TF_CODE_SECONDS[tf_code(P.htf)]
    htf_valid = htf_sec > chart_sec
    if htf_valid:
        hx = _htf_prev(df, htf_sec, P.htf_ema_len)
        hc, he = hx["close"].to_numpy(), hx["ema"].to_numpy()
        htfBias = np.where(gt(hc, he), 1, np.where(lt(hc, he), -1, 0))
    else:
        htfBias = np.zeros(n, int)

    emaS_prev = ta.shift(emaS, P.slope_len)
    stack = np.where(gt(emaF, emaM) & gt(emaM, emaS), "BULLISH",
                     np.where(lt(emaF, emaM) & lt(emaM, emaS), "BEARISH", "MIXED"))
    slope = np.where(gt(emaS, emaS_prev), "RISING", np.where(lt(emaS, emaS_prev), "FALLING", "FLAT"))

    # Régimen
    trendUp = gt(c, emaS) & gt(emaS, emaS_prev) & ge(adxV, P.adx_trend)
    trendDown = lt(c, emaS) & lt(emaS, emaS_prev) & ge(adxV, P.adx_trend)
    highVol = ge(atrPct, P.high_vol_pct)
    regime = np.where(highVol, "HIGH_VOLATILITY", np.where(trendUp, "TREND_UP", np.where(trendDown, "TREND_DOWN", "RANGE")))

    # Puntuaciones (cinco factores de 20 puntos)
    bull, bear = c > o, c < o
    lTrend = 7 * gt(emaF, emaM) + 7 * gt(emaM, emaS) + 6 * gt(c, emaF)
    sTrend = 7 * lt(emaF, emaM) + 7 * lt(emaM, emaS) + 6 * lt(c, emaF)
    hist_prev = ta.shift(hist)
    lMom = np.where(gt(rsiV, 50), np.where(ge(rsiV, P.rsi_ob), 4, 10), 0) + 5 * gt(hist, 0) + 5 * gt(hist, hist_prev)
    sMom = np.where(lt(rsiV, 50), np.where(le(rsiV, P.rsi_os), 4, 10), 0) + 5 * lt(hist, 0) + 5 * lt(hist, hist_prev)
    lStr = 10 * gt(diP, diM) + 10 * (gt(diP, diM) & ge(adxV, P.adx_trend))
    sStr = 10 * gt(diM, diP) + 10 * (gt(diM, diP) & ge(adxV, P.adx_trend))
    lVol = np.where(bull & ge(relVol, 1.5), 20, np.where(bull & ge(relVol, 1.0), 12, np.where(bull, 6, 0)))
    sVol = np.where(bear & ge(relVol, 1.5), 20, np.where(bear & ge(relVol, 1.0), 12, np.where(bear, 6, 0)))
    lHtf = np.where(htfBias == 1, 20, np.where(htfBias == 0, 10, 0))
    sHtf = np.where(htfBias == -1, 20, np.where(htfBias == 0, 10, 0))
    longScore = (lTrend + lMom + lStr + lVol + lHtf).astype(int)
    shortScore = (sTrend + sMom + sStr + sVol + sHtf).astype(int)

    # Estructura de mercado (pivotes confirmados)
    ph, pl = ta.pivots(h, l, P.swing_len)
    swH1 = np.full(n, NaN); swH2 = np.full(n, NaN); swL1 = np.full(n, NaN); swL2 = np.full(n, NaN)
    a1 = a2 = b1 = b2 = NaN
    for i in range(n):
        if not np.isnan(ph[i]):
            a2, a1 = a1, ph[i]
        if not np.isnan(pl[i]):
            b2, b1 = b1, pl[i]
        swH1[i], swH2[i], swL1[i], swL2[i] = a1, a2, b1, b2
    structBias = np.where(gt(swH1, swH2) & gt(swL1, swL2), "BULLISH",
                          np.where(lt(swH1, swH2) & lt(swL1, swL2), "BEARISH", "MIXED"))
    c_prev = ta.shift(c)
    breakUp = ~np.isnan(swH1) & gt(c, swH1) & le(c_prev, swH1)
    breakDown = ~np.isnan(swL1) & lt(c, swL1) & ge(c_prev, swL1)
    structEvent = np.where(breakUp, "BREAK_UP", np.where(breakDown, "BREAK_DOWN", "NONE"))

    # Anatomía de la vela
    o1, h1, l1 = ta.shift(o), ta.shift(h), ta.shift(l)
    pattern = np.array([_pattern(o[i], h[i], l[i], c[i], o1[i], c_prev[i], h1[i], l1[i], atrV[i]) for i in range(n)])
    rng = h - l
    with np.errstate(divide="ignore", invalid="ignore"):
        bodyPct = np.where(rng > 0, np.abs(c - o) / rng * 100, 0.0)
        upPct = np.where(rng > 0, (h - np.maximum(o, c)) / rng * 100, 0.0)
        dnPct = np.where(rng > 0, (np.minimum(o, c) - l) / rng * 100, 0.0)
        rangeAtr = np.where(atrV > 0, rng / atrV, NaN)
        closePos = np.where(rng > 0, (c - l) / rng * 100, 50.0)
        gapPct = np.where(c_prev > 0, (o - c_prev) / c_prev * 100, NaN)
    direction = np.where(bull, "bull", np.where(bear, "bear", "flat"))

    # Setups (solo velas cerradas: aquí todas lo están)
    thr = P.score_threshold
    ls_prev, ss_prev = ta.shift(longScore.astype(float)), ta.shift(shortScore.astype(float))
    blockHV = (regime == "HIGH_VOLATILITY") if P.block_high_vol else np.zeros(n, bool)
    longCond = (longScore >= thr) & lt(ls_prev, thr) & (regime != "TREND_DOWN") & ~blockHV
    shortCond = (shortScore >= thr) & lt(ss_prev, thr) & (regime != "TREND_UP") & ~blockHV
    longSig = longCond & ~shortCond
    shortSig = shortCond & ~longCond

    # Seguimiento de la operación (stop antes que objetivo: conservador)
    side = 0; sideStr = "NONE"; status = "NONE"
    entry = stop = t1 = t2 = NaN; tbar = None; lastBar = None; lastSide = "NONE"
    cols = {k: [] for k in ["t_side", "t_side_str", "t_status", "t_entry", "t_stop", "t_t1", "t_t2", "t_bar",
                            "last_sig_bar", "last_sig_side", "event"]}
    trades: list[dict] = []
    cur: dict | None = None
    for i in range(n):
        before = status
        if side != 0 and i > tbar:
            if side == 1:
                if l[i] <= stop:
                    status = "STOPPED_AFTER_T1" if status == "TARGET_1_HIT" else "STOPPED"; side = 0
                elif h[i] >= t2:
                    status = "TARGET_2_HIT"; side = 0
                elif h[i] >= t1 and status == "ACTIVE":
                    status = "TARGET_1_HIT"
            else:
                if h[i] >= stop:
                    status = "STOPPED_AFTER_T1" if status == "TARGET_1_HIT" else "STOPPED"; side = 0
                elif l[i] <= t2:
                    status = "TARGET_2_HIT"; side = 0
                elif l[i] <= t1 and status == "ACTIVE":
                    status = "TARGET_1_HIT"
            if cur is not None:
                if status == "TARGET_1_HIT" and cur["t1_bar"] is None:
                    cur["t1_bar"] = i
                if side == 0:
                    cur.update(exit_bar=i, result=status)
                    cur = None
        opened_long = opened_short = False
        if longSig[i] and side != 1:
            if cur is not None:  # una señal contraria sustituye a la operación abierta
                cur.update(exit_bar=i, result="REPLACED", exit_price=c[i])
            opened_long = True
            side, sideStr = 1, "LONG"
            entry = c[i]; stop = entry - P.stop_atr * atrV[i]; r = entry - stop
            t1, t2 = entry + P.t1_r * r, entry + P.t2_r * r
            tbar, status, lastBar, lastSide = i, "ACTIVE", i, "LONG"
        elif shortSig[i] and side != -1:
            if cur is not None:
                cur.update(exit_bar=i, result="REPLACED", exit_price=c[i])
            opened_short = True
            side, sideStr = -1, "SHORT"
            entry = c[i]; stop = entry + P.stop_atr * atrV[i]; r = stop - entry
            t1, t2 = entry - P.t1_r * r, entry - P.t2_r * r
            tbar, status, lastBar, lastSide = i, "ACTIVE", i, "SHORT"
        if opened_long or opened_short:
            cur = dict(side=sideStr, entry_bar=i, entry=entry, stop=stop, t1=t1, t2=t2,
                       t1_bar=None, exit_bar=None, result="OPEN", exit_price=None)
            trades.append(cur)
        trade_update = status != before and not opened_long and not opened_short
        event = "LONG_SETUP" if opened_long else "SHORT_SETUP" if opened_short else "TRADE_UPDATE" if trade_update else "BAR_CLOSE"
        for k, val in zip(cols, [side, sideStr, status, entry, stop, t1, t2, tbar, lastBar, lastSide, event]):
            cols[k].append(val)

    out = df.copy()
    out = out.assign(
        ema_fast=emaF, ema_mid=emaM, ema_slow=emaS, stack=stack, slow_slope=slope, htf_bias=htfBias,
        rsi=rsiV, macd_hist=hist, macd_hist_prev=hist_prev, adx=adxV, di_plus=diP, di_minus=diM,
        atr=atrV, atr_percentile=atrPct, rel_vol=relVol, vwap=vwapV, prev_day_high=pdh, prev_day_low=pdl,
        regime=regime, long_score=longScore, short_score=shortScore,
        l_trend=lTrend, l_mom=lMom, l_str=lStr, l_vol=lVol, l_htf=lHtf,
        s_trend=sTrend, s_mom=sMom, s_str=sStr, s_vol=sVol, s_htf=sHtf,
        swing_high=swH1, swing_high_prev=swH2, swing_low=swL1, swing_low_prev=swL2,
        struct_bias=structBias, struct_event=structEvent,
        direction=direction, pattern=pattern, body_pct=bodyPct, upper_wick_pct=upPct, lower_wick_pct=dnPct,
        range_atr=rangeAtr, close_position_pct=closePos, gap_pct=gapPct,
        closed_above_prev_high=gt(c, h1), closed_below_prev_low=lt(c, l1),
        **cols,
    )
    out["htf_bias_str"] = ("N/A" if not htf_valid else None)
    if htf_valid:
        out["htf_bias_str"] = np.where(htfBias == 1, "UP", np.where(htfBias == -1, "DOWN", "FLAT"))
    return Lens(df=out, trades=trades, params=P, tf=tfc, symbol=symbol, price_decimals=_decimals(c))


# ─────────────────────────────────────────────────────────────────────────────
# Paquete JSON para el agente (schema ai-agent-lens/v1)
# ─────────────────────────────────────────────────────────────────────────────
def _num(x, d):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    r = round(float(x), d)
    return int(r) if d == 0 else r


def packet(lens: Lens, i: int) -> dict:
    """Paquete JSON de la vela i, idéntico en estructura al que envía el indicador."""
    d = lens.df
    row = d.iloc[i]
    P = lens.params
    pd_ = lens.price_decimals
    fp = lambda x: _num(x, pd_)  # noqa: E731
    sec = TF_CODE_SECONDS[lens.tf]
    close_time = (row["time"] + pd.Timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%SZ")
    state = (f"LENS|{lens.symbol.split(':')[-1]}|{lens.tf}|{row['regime']}|L{row['long_score']}|"
             f"S{row['short_score']}|{row['t_side_str']}:{row['t_status']}")
    recent = []
    for k in range(P.recent_count - 1, -1, -1):
        j = i - k
        if j < 0:
            continue
        r = d.iloc[j]
        recent.append({"bars_ago": k, "o": fp(r["open"]), "h": fp(r["high"]), "l": fp(r["low"]), "c": fp(r["close"]),
                       "v": _num(r["volume"], 4), "dir": r["direction"], "body_pct": _num(r["body_pct"], 1),
                       "range_atr": _num(r["range_atr"], 2), "pattern": r["pattern"]})
    tbar = row["t_bar"]
    lbar = row["last_sig_bar"]
    return {
        "schema": SCHEMA,
        "event": row["event"],
        "symbol": lens.symbol,
        "timeframe": lens.tf,
        "time": close_time,
        "state_code": state,
        "candle": {
            "open": fp(row["open"]), "high": fp(row["high"]), "low": fp(row["low"]), "close": fp(row["close"]),
            "volume": _num(row["volume"], 4), "direction": row["direction"],
            "body_pct": _num(row["body_pct"], 1), "upper_wick_pct": _num(row["upper_wick_pct"], 1),
            "lower_wick_pct": _num(row["lower_wick_pct"], 1), "range_atr": _num(row["range_atr"], 2),
            "close_position_pct": _num(row["close_position_pct"], 1), "gap_pct": _num(row["gap_pct"], 3),
            "closed_above_prev_high": bool(row["closed_above_prev_high"]),
            "closed_below_prev_low": bool(row["closed_below_prev_low"]),
            "pattern": row["pattern"],
        },
        "recent_candles": recent,
        "regime": row["regime"],
        "trend": {"ema_fast": fp(row["ema_fast"]), "ema_mid": fp(row["ema_mid"]), "ema_slow": fp(row["ema_slow"]),
                  "stack": row["stack"], "slow_slope": row["slow_slope"], "htf": P.htf, "htf_bias": row["htf_bias_str"]},
        "momentum": {"rsi": _num(row["rsi"], 2), "macd_hist": _num(row["macd_hist"], 6),
                     "macd_hist_rising": bool(gt(row["macd_hist"], row["macd_hist_prev"]))},
        "strength": {"adx": _num(row["adx"], 2), "di_plus": _num(row["di_plus"], 2), "di_minus": _num(row["di_minus"], 2)},
        "volatility": {"atr": fp(row["atr"]), "atr_percentile": _num(row["atr_percentile"], 1)},
        "volume": {"relative": _num(row["rel_vol"], 2)},
        "structure": {"bias": row["struct_bias"], "event": row["struct_event"],
                      "last_swing_high": fp(row["swing_high"]), "last_swing_low": fp(row["swing_low"])},
        "levels": {"prev_day_high": fp(row["prev_day_high"]), "prev_day_low": fp(row["prev_day_low"]),
                   "vwap": fp(row["vwap"])},
        "scores": {
            "long": int(row["long_score"]), "short": int(row["short_score"]), "threshold": P.score_threshold,
            "long_factors": {"trend": int(row["l_trend"]), "momentum": int(row["l_mom"]), "strength": int(row["l_str"]),
                             "volume": int(row["l_vol"]), "htf": int(row["l_htf"])},
            "short_factors": {"trend": int(row["s_trend"]), "momentum": int(row["s_mom"]), "strength": int(row["s_str"]),
                              "volume": int(row["s_vol"]), "htf": int(row["s_htf"])},
        },
        "trade": {"side": row["t_side_str"], "status": row["t_status"], "entry": fp(row["t_entry"]),
                  "stop": fp(row["t_stop"]), "target_1": fp(row["t_t1"]), "target_2": fp(row["t_t2"]),
                  "bars_since_entry": None if tbar is None or pd.isna(tbar) else int(i - tbar)},
        "last_setup": {"side": row["last_sig_side"],
                       "bars_ago": None if lbar is None or pd.isna(lbar) else int(i - lbar)},
    }
