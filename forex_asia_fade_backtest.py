#!/usr/bin/env python3
"""
Backtest EUR/USD: ruptura fallida del rango asiático en Londres (protocolo v1.0)
+ variantes mejoradas pre-declaradas.

Datos: ticks Dukascopy (bid/ask, UTC), caché en ./dukascopy_cache/
Uso:
  python forex_asia_fade_backtest.py --download 2021-01-01 2025-12-31   # descarga ticks
  python forex_asia_fade_backtest.py --run 2021-01-01 2025-12-31        # backtest
  python forex_asia_fade_backtest.py --synthetic                        # prueba de lógica

Reglas (del protocolo): señales con precio medio en M5; ejecución con bid/ask.
Variantes (fijadas ANTES de ver resultados, sin optimizar):
  BASE      : protocolo tal cual
  FVG       : Contraste B (FVG asiático pendiente entre entrada y objetivo)
  RANGO     : solo si amplitud asiática entre 15 y 60 pips (filtra días sin recorrido / de tendencia)
  FVG+RANGO : ambas condiciones
"""
import argparse, lzma, struct, sys, os, datetime as dt
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

PIP = 1e-4
LON = ZoneInfo("Europe/London")
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dukascopy_cache")
COST_RT = 0.2        # comisión+deslizamiento supuesto, pips ida y vuelta (spread ya está en bid/ask)
MIN_RR = 1.5
RANGE_MIN, RANGE_MAX = 15.0, 60.0
SPLITS = {"desarrollo 2021-22": ("2021-01-01", "2022-12-31"),
          "validación 2023-24": ("2023-01-01", "2024-12-31"),
          "prueba final 2025": ("2025-01-01", "2025-12-31")}

# ---------------------------------------------------------------- datos
def bi5_url(d, h):
    return f"https://datafeed.dukascopy.com/datafeed/EURUSD/{d.year}/{d.month-1:02d}/{d.day:02d}/{h:02d}h_ticks.bi5"

def fetch_hour(args):
    import urllib.request
    d, h = args
    p = os.path.join(CACHE, f"{d:%Y%m%d}_{h:02d}.bi5")
    if os.path.exists(p):
        return
    for _ in range(3):
        try:
            data = urllib.request.urlopen(bi5_url(d, h), timeout=30).read()
            open(p, "wb").write(data); return
        except Exception:
            pass
    print("fallo descarga", d, h, file=sys.stderr)

def download(start, end):
    os.makedirs(CACHE, exist_ok=True)
    jobs = [(d.date(), h) for d in pd.bdate_range(start, end) for h in range(0, 12)]
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(fetch_hour, jobs))

def load_ticks(d):
    """DataFrame ticks del día (UTC 00-12): t (ns UTC), bid, ask. None si faltan datos críticos."""
    frames = []
    for h in range(0, 12):
        p = os.path.join(CACHE, f"{d:%Y%m%d}_{h:02d}.bi5")
        if not os.path.exists(p): continue
        raw = open(p, "rb").read()
        if not raw: continue
        buf = lzma.decompress(raw)
        a = np.frombuffer(buf, dtype=">u4,>u4,>u4,>f4,>f4")
        t0 = pd.Timestamp(d, tz="UTC") + pd.Timedelta(hours=h)
        frames.append(pd.DataFrame({"t": t0 + pd.to_timedelta(a["f0"].astype("int64"), unit="ms"),
                                    "ask": a["f1"] / 1e5, "bid": a["f2"] / 1e5}))
    return pd.concat(frames, ignore_index=True) if frames else None

def synthetic_ticks(d, rng):
    """Random walk con spread ~0.8 pip, para probar lógica."""
    t0 = pd.Timestamp(d, tz="UTC")
    n = 12 * 3600 // 3
    t = t0 + pd.to_timedelta(np.arange(n) * 3000 + rng.integers(0, 2000, n), unit="ms")
    mid = 1.10 + np.cumsum(rng.normal(0, 0.00003, n))
    sp = 0.4 * PIP + rng.exponential(0.1 * PIP, n)
    return pd.DataFrame({"t": t, "bid": mid - sp, "ask": mid + sp})

# ---------------------------------------------------------------- estrategia
def m5_mid(ticks):
    mid = ((ticks.bid + ticks.ask) / 2).values
    s = pd.Series(mid, index=ticks.t)
    b = s.resample("5min").ohlc().dropna()
    b.columns = ["o", "h", "l", "c"]
    return b

def find_fvg_pending(bars, asia_end, confirm_end):
    """Primer FVG M5 formado en Asia que sigue sin tocarse al final de Asia y hasta confirm_end.
    Devuelve (lo, hi) o None. Usa solo velas cerradas."""
    asia = bars[bars.index < asia_end]
    h, l = asia.h.values, asia.l.values
    for i in range(2, len(asia)):
        lo = hi = None
        if l[i] > h[i - 2]: lo, hi = h[i - 2], l[i]          # alcista
        elif h[i] < l[i - 2]: lo, hi = h[i], l[i - 2]        # bajista
        else: continue
        form_end = asia.index[i] + pd.Timedelta("5min")
        later = bars[(bars.index >= form_end) & (bars.index + pd.Timedelta("5min") <= confirm_end)]
        touched = ((later.l <= hi) & (later.h >= lo)).any()
        if not touched:
            return lo, hi
    return None

def simulate_day(d, ticks, delay_min=0, extra_cost=0.0):
    """Devuelve dict de contexto + dict por variante {nombre: trade|None}."""
    loc = pd.Timestamp(dt.datetime(d.year, d.month, d.day, 8, 0), tz=LON)
    asia_end = loc.tz_convert("UTC")
    win_end = asia_end + pd.Timedelta("2h")
    exit_t = asia_end + pd.Timedelta("3h")
    day0 = pd.Timestamp(d, tz="UTC")
    ticks = ticks[(ticks.t >= day0) & (ticks.t < exit_t + pd.Timedelta("10min"))]
    if ticks.empty or ticks.t.min() > day0 + pd.Timedelta("30min"):
        return {"estado": "no evaluable"}, {}
    bars = m5_mid(ticks)
    asia = bars[bars.index < asia_end]
    if len(asia) < 0.9 * (asia_end - day0) / pd.Timedelta("5min"):
        return {"estado": "no evaluable"}, {}
    aH, aL = asia.h.max(), asia.l.min()
    mid = (aH + aL) / 2
    width = (aH - aL) / PIP
    ctx = {"estado": "ok", "asia_pips": width}

    # señal ----------------------------------------------------
    scan = bars[(bars.index >= asia_end) & (bars.index < win_end)]
    brk_i = None; side = None
    for i, (t, r) in enumerate(scan.iterrows()):
        up, dn = r.h >= aH + PIP, r.l <= aL - PIP
        if up and dn: ctx["motivo"] = "ambos extremos"; return ctx, {}
        if up or dn:
            brk_i, side = i, ("short" if up else "long"); break
    if brk_i is None: ctx["motivo"] = "sin ruptura"; return ctx, {}
    conf_i = None
    for j in range(brk_i, min(brk_i + 3, len(scan))):
        r = scan.iloc[j]
        if j > brk_i and (r.h >= aH + PIP and side == "long" or r.l <= aL - PIP and side == "short"):
            ctx["motivo"] = "ambos extremos"; return ctx, {}
        if aL < r.c < aH: conf_i = j; break
    if conf_i is None: ctx["motivo"] = "sin confirmación"; return ctx, {}
    conf_end = scan.index[conf_i] + pd.Timedelta("5min")
    if conf_end >= win_end: ctx["motivo"] = "confirmación fuera de ventana"; return ctx, {}
    leg = scan.iloc[brk_i:conf_i + 1]
    extreme = leg.h.max() if side == "short" else leg.l.min()
    stop = extreme + PIP if side == "short" else extreme - PIP
    entry_t = conf_end + pd.Timedelta(minutes=delay_min)
    if entry_t >= win_end: ctx["motivo"] = "entrada fuera de ventana"; return ctx, {}

    # ejecución ------------------------------------------------
    tk = ticks[ticks.t >= entry_t]
    if tk.empty: ctx["motivo"] = "sin cotización"; return ctx, {}
    e = tk.iloc[0]
    entry = e.ask if side == "long" else e.bid
    long = side == "long"
    if (long and (entry >= mid or entry <= stop)) or (not long and (entry <= mid or entry >= stop)):
        ctx["motivo"] = "precio ya en objetivo/stop"; return ctx, {}
    cost = (COST_RT + extra_cost)
    risk = abs(entry - stop) / PIP + cost
    reward = abs(mid - entry) / PIP - cost
    if reward / risk < MIN_RR: ctx["motivo"] = "R:R < 1,5"; return ctx, {}

    fvg = find_fvg_pending(bars, asia_end, conf_end)
    fvg_ok = fvg is not None and min(entry, mid) <= fvg[0] and fvg[1] <= max(entry, mid)
    range_ok = RANGE_MIN <= width <= RANGE_MAX

    # salida con ticks (lado ejecutable) ----------------------
    after = tk.iloc[1:]
    after = after[after.t <= exit_t + pd.Timedelta("10min")]
    tt = after.t.values; bid = after.bid.values; ask = after.ask.values
    exit_ns = np.datetime64(exit_t.tz_convert(None) if exit_t.tzinfo else exit_t)
    tt = tt.astype("datetime64[ns]") if tt.dtype.kind != "M" else tt
    t_arr = pd.to_datetime(after.t).dt.tz_convert("UTC").dt.tz_localize(None).values
    px = bid if long else ask
    if long: hit_stop, hit_tgt = bid <= stop, bid >= mid
    else:    hit_stop, hit_tgt = ask >= stop, ask <= mid
    timed = t_arr >= exit_ns
    ev = hit_stop | hit_tgt | timed
    k = np.argmax(ev) if ev.any() else None
    if k is None: ctx["motivo"] = "sin datos de salida"; return {"estado": "no evaluable"}, {}
    if hit_stop[k] and hit_tgt[k]: reason, fill = "stop", px[k]
    elif hit_stop[k]: reason, fill = "stop", px[k]            # puede haber salto: primer precio disponible
    elif hit_tgt[k]: reason, fill = "objetivo", mid           # límite: sin mejora supuesta
    else: reason, fill = "horario", px[k]
    pips = ((fill - entry) if long else (entry - fill)) / PIP - cost
    trade = {"lado": side, "entry": entry, "stop": stop, "tgt": mid, "salida": reason,
             "pips": pips, "R": pips / risk, "risk": risk}
    out = {"BASE": trade}
    if fvg_ok: out["FVG"] = trade
    if range_ok: out["RANGO"] = trade
    if fvg_ok and range_ok: out["FVG+RANGO"] = trade
    return ctx, out

# ---------------------------------------------------------------- métricas
def week_bootstrap(df_days, col="R", n=2000, seed=0):
    g = df_days.groupby(df_days.date.dt.to_period("W"))[col].sum()
    rng = np.random.default_rng(seed)
    if len(g) < 5: return (np.nan, np.nan)
    m = [rng.choice(g.values, len(g)).sum() / len(df_days) for _ in range(n)]
    return np.percentile(m, [2.5, 97.5])

def report(rows, variants, label):
    print(f"\n=== {label} ===")
    for v in variants:
        sel = [r for r in rows if r["estado"] == "ok"]
        days = pd.DataFrame([{"date": pd.Timestamp(r["date"]), "R": r.get(v, {}).get("R", 0.0),
                              "pips": r.get(v, {}).get("pips", 0.0), "t": v in r} for r in sel])
        if days.empty: print(v, "sin días"); continue
        tr = days[days.t]
        n_tr = len(tr)
        if n_tr == 0: print(f"{v:10s} sin operaciones"); continue
        cum = days.R.cumsum(); dd = (cum - cum.cummax()).min()
        lo, hi = week_bootstrap(days)
        trades = [r[v] for r in sel if v in r]
        by = lambda k: {s: round(np.mean([t["R"] for t in trades if t["lado"] == s] or [np.nan]), 3) for s in ("long", "short")}
        ex = pd.Series([t["salida"] for t in trades]).value_counts().to_dict()
        print(f"{v:10s} días={len(days)} ops={n_tr} netPips={tr.pips.sum():7.1f} R={tr.R.sum():7.1f} "
              f"R/op={tr.R.mean():6.3f} R/día={days.R.mean():6.4f} IC95(R/día)=[{lo:.4f},{hi:.4f}] "
              f"acierto={np.mean(tr.R > 0):.2%} DDmax={dd:.1f}R | lado {by(0)} | salidas {ex}")

def run(start, end, synthetic=False, delay_min=0, extra=0.0):
    rng = np.random.default_rng(1)
    rows = []
    for d in pd.bdate_range(start, end):
        d = d.date()
        tk = synthetic_ticks(d, rng) if synthetic else load_ticks(d)
        if tk is None: rows.append({"date": d, "estado": "no evaluable"}); continue
        tk = tk.sort_values("t").drop_duplicates()
        tk = tk[tk.ask > tk.bid]                                # spreads negativos/anómalos fuera
        ctx, out = simulate_day(d, tk, delay_min, extra)
        rows.append({"date": d, **ctx, **out})
    return rows

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", nargs=2); ap.add_argument("--run", nargs=2)
    ap.add_argument("--synthetic", action="store_true")
    a = ap.parse_args()
    V = ["BASE", "FVG", "RANGO", "FVG+RANGO"]
    if a.download: download(*a.download)
    elif a.synthetic:
        rows = run("2024-03-01", "2024-04-30", synthetic=True)
        print("días evaluables:", sum(r["estado"] == "ok" for r in rows))
        print(pd.Series([r.get("motivo", "OPERA") for r in rows if r["estado"] == "ok"]).value_counts())
        report(rows, V, "SINTÉTICO (solo prueba de lógica, sin significado económico)")
    elif a.run:
        for name, (s, e) in SPLITS.items():
            s2, e2 = max(s, a.run[0]), min(e, a.run[1])
            if s2 > e2: continue
            rows = run(s2, e2)
            report(rows, V, name)
            for lbl, dm, ex in [("estrés +0,5 pip", 0, 0.5), ("estrés +1 pip", 0, 1.0), ("entrada +15 min", 15, 0.0)]:
                report(run(s2, e2, delay_min=dm, extra=ex), ["BASE", "FVG+RANGO"], f"{name} · {lbl}")
