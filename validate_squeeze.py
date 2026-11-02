#!/usr/bin/env python3
"""Validación walk-forward de Squeeze Momentum, sin hacernos trampas.

1. Se define de antemano una rejilla pequeña de variantes (abajo, GRID).
2. Cada variante se evalúa SOLO en el periodo de ajuste (in-sample, por defecto
   hasta 2024-12-31), juntando todas las operaciones de todos los mercados.
3. Se elige UNA: la de mayor estadístico t del R medio conjunto (premia ganar
   y también tener suficientes operaciones).
4. Esa variante se prueba UNA vez en el periodo de validación (out-of-sample,
   desde 2025-01-01). Se aprueba solo si el R medio es > 0 en TODOS los mercados.

Los indicadores se calculan sobre todo el histórico (son causales, lo comprueban
los tests), y el backtest de cada periodo empieza con capital nuevo.

Uso:
  python3 validate_squeeze.py
  python3 validate_squeeze.py --symbols PF_XBTUSD PF_ETHUSD PF_SOLUSD PF_XRPUSD
  python3 validate_squeeze.py --split 2024-07-01 --costs spot
"""
from __future__ import annotations

import argparse
import itertools
from pathlib import Path

import numpy as np
import pandas as pd

from smc.backtest import BTConfig, run_signal_backtest, summarize
from smc.data import load_or_download
from smc.squeeze import SqueezeParams, generate_squeeze_signals

GRID = {
    "tf": ["4h", "1d"],
    "exit_mode": ["color", "zero"],
    "trend_filter": ["none", "ema"],
    "min_squeeze_bars": [3, 6, 10],
    "replicate_bug": [True, False],
}


def window(df, entries, exits, start, end):
    """Recorta datos y señales a [start, end) re-indexando las barras."""
    idx = np.flatnonzero((df.index >= start) & (df.index < end))
    if len(idx) == 0:
        return None
    a, b = idx[0], idx[-1] + 1
    e = entries[(entries["bar"] >= a) & (entries["bar"] < b)].copy()
    e["bar"] -= a
    return df.iloc[a:b], e, exits.iloc[a:b]


def pooled(rs: list[np.ndarray]) -> tuple[int, float, float]:
    r = np.concatenate(rs) if rs else np.array([])
    n = len(r)
    if n < 2 or r.std(ddof=1) == 0:
        return n, (r.mean() if n else np.nan), np.nan
    return n, r.mean(), r.mean() / (r.std(ddof=1) / np.sqrt(n))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", nargs="+", default=["PF_XBTUSD", "PF_ETHUSD", "PF_SOLUSD"])
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--split", default="2025-01-01", help="inicio del periodo de validación")
    ap.add_argument("--costs", default="futures", choices=["futures", "spot", "zero"])
    ap.add_argument("--min_trades", type=int, default=45, help="mínimo de operaciones in-sample (conjunto)")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--out", default="results")
    a = ap.parse_args()

    split = pd.Timestamp(a.split, tz="UTC")
    far_past, far_future = pd.Timestamp("1970-01-01", tz="UTC"), pd.Timestamp("2100-01-01", tz="UTC")
    cfg = BTConfig(costs=a.costs)
    Path(a.out).mkdir(exist_ok=True)

    data = {}
    for tf in GRID["tf"]:
        for s in a.symbols:
            data[(s, tf)] = load_or_download(None, s, tf, a.start, None, a.data_dir)[0]

    keys = [k for k in GRID if k != "tf"]
    combos = [dict(zip(["tf"] + keys, v)) for v in itertools.product(GRID["tf"], *(GRID[k] for k in keys))]
    print(f"Evaluando {len(combos)} variantes x {len(a.symbols)} mercados "
          f"(ajuste: hasta {a.split}, validación: desde {a.split})...\n")

    rows, signals = [], {}
    for i, c in enumerate(combos):
        p = SqueezeParams(**{k: c[k] for k in keys})
        rs, per = [], {}
        for s in a.symbols:
            df = data[(s, c["tf"])]
            e, x = generate_squeeze_signals(df, p)
            signals[(i, s)] = (e, x)
            w = window(df, e, x, far_past, split)
            tr, _ = run_signal_backtest(*w, cfg) if w else (pd.DataFrame(), None)
            r = tr["r"].to_numpy() if len(tr) else np.array([])
            rs.append(r)
            per[s] = r.mean() if len(r) else np.nan
        n, mean, t = pooled(rs)
        rows.append({"id": i, **c, "is_trades": n, "is_avg_R": mean, "is_t": t,
                     **{f"is_R_{s}": v for s, v in per.items()}})

    res = pd.DataFrame(rows)
    res.to_csv(Path(a.out) / "validate_squeeze_in_sample.csv", index=False)
    elig = res[res["is_trades"] >= a.min_trades].sort_values("is_t", ascending=False)
    if elig.empty:
        print("Ninguna variante alcanza el mínimo de operaciones in-sample.")
        return

    cols = ["tf", "exit_mode", "trend_filter", "min_squeeze_bars", "replicate_bug",
            "is_trades", "is_avg_R", "is_t"] + [f"is_R_{s}" for s in a.symbols]
    with pd.option_context("display.width", 200, "display.max_columns", 30, "display.float_format", "{:.3f}".format):
        print("=== TOP 10 EN AJUSTE (in-sample) ===")
        print(elig[cols].head(10).to_string(index=False))
    n_sig = int((res["is_t"] > 2).sum())
    print(f"\nVariantes con t > 2 in-sample: {n_sig} de {len(res)} "
          f"(por puro azar se esperarían ~{0.023 * len(res):.1f})")

    best = elig.iloc[0]
    bid = int(best["id"])
    p = SqueezeParams(**{k: best[k] for k in keys})
    print(f"\n=== VARIANTE ELEGIDA (solo con datos de ajuste) ===\n"
          f"  tf={best['tf']} exit_mode={p.exit_mode} trend_filter={p.trend_filter} "
          f"min_squeeze_bars={p.min_squeeze_bars} replicate_bug={p.replicate_bug}")

    print(f"\n=== VALIDACIÓN (out-of-sample, desde {a.split}) | costes={a.costs} ===")
    oos_rows, rs = [], []
    for s in a.symbols:
        df = data[(s, best["tf"])]
        e, x = signals[(bid, s)]
        w = window(df, e, x, split, far_future)
        if w is None:
            continue
        tr, eq = run_signal_backtest(*w, cfg)
        m = summarize(tr, eq, w[0], cfg.initial_equity)
        rs.append(tr["r"].to_numpy() if len(tr) else np.array([]))
        oos_rows.append({"mercado": s, "trades": m["trades"], "win%": m.get("win_rate_pct", np.nan),
                         "avg_R": m.get("avg_R", np.nan), "PF": m.get("profit_factor", np.nan),
                         "ret%": m["return_pct"], "DD%": m["max_drawdown_pct"],
                         "buy&hold%": m["buy_hold_pct"]})
    oos = pd.DataFrame(oos_rows)
    oos.to_csv(Path(a.out) / "validate_squeeze_out_of_sample.csv", index=False)
    with pd.option_context("display.float_format", "{:.2f}".format, "display.width", 200):
        print(oos.to_string(index=False))
    n, mean, t = pooled(rs)
    print(f"\n  Conjunto: {n} operaciones | R medio {mean:+.3f} | t = {t:.2f}")

    ok = len(oos) == len(a.symbols) and (oos["avg_R"] > 0).all()
    print("\n=== VEREDICTO ===")
    if ok and t > 2:
        print("  APROBADA: positiva en todos los mercados y estadísticamente sólida.")
        print("  Siguiente paso: paper trading varias semanas antes de dinero real.")
    elif ok:
        print("  DUDOSA: positiva en todos los mercados, pero sin significación (t <= 2).")
        print("  Podría ser azar. Solo paper trading, nada de dinero real todavía.")
    else:
        print("  RECHAZADA: no es positiva en todos los mercados fuera de muestra.")

    print("\n  (Referencia, NO para elegir) validación de las 5 mejores in-sample:")
    for _, b in elig.head(5).iterrows():
        rs = []
        for s in a.symbols:
            df = data[(s, b["tf"])]
            e, x = signals[(int(b["id"]), s)]
            w = window(df, e, x, split, far_future)
            if w:
                tr, _ = run_signal_backtest(*w, cfg)
                rs.append(tr["r"].to_numpy() if len(tr) else np.array([]))
        n, mean, t = pooled(rs)
        print(f"   {b['tf']:>3} {b['exit_mode']:>5} {b['trend_filter']:>4} sqz>={int(b['min_squeeze_bars']):<2} "
              f"bug={'sí' if b['replicate_bug'] else 'no'}  -> {n:>3} ops, R medio {mean:+.3f}, t={t:.2f}")
    print(f"\nDetalle guardado en {a.out}/validate_squeeze_*.csv")


if __name__ == "__main__":
    main()
