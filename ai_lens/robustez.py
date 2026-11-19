"""¿Es la ventaja real? Pruebas de robustez del indicador antes de pensar en dinero real.

    python -m ai_lens.robustez

1. Rendimiento por periodo (incluye 2022, que no se usó para mirar nada).
2. Comparación con entradas al azar: mismo stop (1,5 ATR), mismo objetivo (3R), mismos costes.
   a) mismas velas que el indicador pero dirección al azar -> ¿acierta la dirección?
   b) velas y dirección al azar -> ¿es mejor que operar sin criterio?
3. Sensibilidad a parámetros (umbral, stop) y a costes.
4. Traducción a % de la cuenta con 0,5 % y 1 % de riesgo por operación.

Cada operación se evalúa de forma independiente (stop o T2; abierta al final = valor de mercado).
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from . import backtest

ASSETS = ["BTC", "ETH", "SOL"]


def _eval(h, l, c, i, side, entry, stop, t2, cost_r):
    """R neto de una operación: stop primero si la vela toca los dos."""
    n = len(c)
    risk = abs(entry - stop)
    for j in range(i + 1, n):
        if side == 1:
            if l[j] <= stop:
                return -1 - cost_r, j
            if h[j] >= t2:
                return (t2 - entry) / risk - cost_r, j
        else:
            if h[j] >= stop:
                return -1 - cost_r, j
            if l[j] <= t2:
                return (entry - t2) / risk - cost_r, j
    return side * (c[-1] - entry) / risk - cost_r, n - 1


_CACHE: dict = {}


def trades_for(asset, start, end, threshold=70, stop_atr=1.5, bps=7.0, rng=None, mode="indicator"):
    key = (asset, start, end, threshold, stop_atr)
    if key not in _CACHE:
        _CACHE[key] = backtest.build(asset, "4h", start, end, "240", threshold, stop_atr=stop_atr)
    lens, i0 = _CACHE[key]
    d = lens.df
    h, l, c, atr, t = (d[k].to_numpy() for k in ["high", "low", "close", "atr", "time"])
    ev = d["event"].to_numpy()
    idx = [i for i in range(i0, len(d)) if ev[i] in ("LONG_SETUP", "SHORT_SETUP")]
    sides = [1 if ev[i] == "LONG_SETUP" else -1 for i in idx]
    if mode == "random_side":
        sides = list(rng.choice([1, -1], size=len(idx)))
    elif mode == "random_all":
        idx = sorted(rng.choice(np.arange(max(i0, 300), len(d) - 1), size=len(idx), replace=False))
        sides = list(rng.choice([1, -1], size=len(idx)))
    out = []
    for i, s in zip(idx, sides):
        entry = c[i]
        stop = entry - s * stop_atr * atr[i]
        risk = abs(entry - stop)
        t2 = entry + s * 3.0 * risk
        cost = bps * 2 / 1e4 * entry / risk
        r, j = _eval(h, l, c, i, s, entry, stop, t2, cost)
        out.append((asset, t[i], t[j], s, r))
    return out


def summarize(tr: list) -> dict:
    """Estadísticas de una lista de operaciones (asset, entrada, salida, lado, R)."""
    df = pd.DataFrame(tr, columns=["asset", "entry", "exit", "side", "r"]).sort_values("exit")
    r = df["r"].to_numpy()
    eq = np.cumsum(r)
    dd = float(np.max(np.maximum.accumulate(np.maximum(eq, 0)) - eq)) if len(r) else 0.0
    streak = best = 0
    for x in r:
        streak = streak + 1 if x < 0 else 0
        best = max(best, streak)
    pf = r[r > 0].sum() / -r[r < 0].sum() if (r < 0).any() else None

    def acct(risk):
        e, peak, mdd = 1.0, 1.0, 0.0
        for x in r:
            e *= 1 + x * risk
            peak = max(peak, e)
            mdd = max(mdd, 1 - e / peak)
        return round((e - 1) * 100, 1), round(-mdd * 100, 1)
    a05, d05 = acct(0.005)
    a1, d1 = acct(0.01)
    return dict(ops=int(len(r)), aciertos_pct=round(float((r > 0).mean() * 100), 1) if len(r) else None,
                r_neto=round(float(r.sum()), 1), r_op=round(float(r.mean()), 3) if len(r) else None,
                pf=round(float(pf), 2) if pf is not None else None, dd_r=round(dd, 1), racha=int(best),
                cuenta_05_pct=a05, dd_05_pct=d05, cuenta_1_pct=a1, dd_1_pct=d1)


def compute(start="2022-06-01", end="2026-11-18 00:00", sims=300) -> dict:
    """Todas las pruebas, como tablas listas para imprimir o exportar."""
    base = sum((trades_for(x, start, end) for x in ASSETS), [])
    df = pd.DataFrame(base, columns=["asset", "entry", "exit", "side", "r"]).sort_values("exit")
    desglose = [dict(grupo="Total", tipo="total", **summarize(base))]
    desglose += [dict(grupo=x, tipo="activo", **summarize([t for t in base if t[0] == x])) for x in ASSETS]
    desglose += [dict(grupo=str(y), tipo="año", **summarize(list(g.itertuples(index=False, name=None))))
                 for y, g in df.groupby(df["exit"].dt.year)]
    desglose += [dict(grupo=n, tipo="lado", **summarize([t for t in base if t[3] == s]))
                 for s, n in [(1, "Solo LONG"), (-1, "Solo SHORT")]]

    rng = np.random.default_rng(42)
    tot = float(df["r"].sum())
    azar = []
    for mode, desc in [("random_side", "Mismas velas, dirección al azar"), ("random_all", "Velas y dirección al azar")]:
        res = np.array([sum(t[4] for x in ASSETS for t in trades_for(x, start, end, rng=rng, mode=mode))
                        for _ in range(sims)])
        azar.append(dict(prueba=desc, simulaciones=sims, r_medio=round(float(res.mean()), 1),
                         p5=round(float(np.percentile(res, 5)), 1), p95=round(float(np.percentile(res, 95)), 1),
                         indicador=round(tot, 1), prob_pct=round(float((res >= tot).mean() * 100), 1)))

    sens = []
    for thr in [60, 65, 70, 75, 80]:
        sens.append(dict(variante=f"Umbral {thr}", tipo="umbral", actual=thr == 70,
                         **summarize(sum((trades_for(x, start, end, threshold=thr) for x in ASSETS), []))))
    for st in [1.0, 1.5, 2.0, 2.5]:
        sens.append(dict(variante=f"Stop {st:g} ATR".replace(".", ","), tipo="stop", actual=st == 1.5,
                         **summarize(sum((trades_for(x, start, end, stop_atr=st) for x in ASSETS), []))))
    for bps in [0.0, 7.0, 15.0]:
        sens.append(dict(variante=f"Costes {bps:g} pb por lado", tipo="costes", actual=bps == 7.0,
                         **summarize(sum((trades_for(x, start, end, bps=bps) for x in ASSETS), []))))

    eq = df.assign(r_acum=df["r"].cumsum())
    e, cuenta = 1.0, []
    for x in eq["r"]:
        e *= 1 + x * 0.005
        cuenta.append(round((e - 1) * 100, 2))
    curva = [dict(fecha=t.strftime("%Y-%m-%dT%H:%M:%SZ"), r_acum=round(float(v), 2), cuenta_05_pct=c)
             for t, v, c in zip(eq["exit"], eq["r_acum"], cuenta)]
    mensual = [dict(mes=m, r_neto=round(float(v), 1)) for m, v in
               df.groupby(df["exit"].dt.strftime("%Y-%m"))["r"].sum().items()]
    return dict(periodo=[dict(desde=start[:10], hasta=end[:10], activos=", ".join(ASSETS), tf="4h",
                              costes_bps_lado=7, riesgo_ejemplo_pct=0.5)],
                desglose=desglose, azar=azar, sensibilidad=sens, curva=curva, mensual=mensual)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-06-01")
    ap.add_argument("--end", default="2026-11-18 00:00")
    ap.add_argument("--sims", type=int, default=300)
    a = ap.parse_args()
    pd.set_option("display.width", 220)
    res = compute(a.start, a.end, a.sims)
    print(f"\n### 1. Indicador, todas las señales, BTC+ETH+SOL 4h, {a.start} -> {a.end}, costes 7 bps/lado\n")
    print(pd.DataFrame(res["desglose"]).set_index("grupo").to_string())
    print(f"\n### 2. Contra el azar ({a.sims} simulaciones)\n")
    print(pd.DataFrame(res["azar"]).set_index("prueba").to_string())
    print("\n### 3. Sensibilidad\n")
    print(pd.DataFrame(res["sensibilidad"]).set_index("variante").to_string())


if __name__ == "__main__":
    main()
