#!/usr/bin/env python3
"""Backtest de Squeeze Momentum (LazyBear) con reglas de entrada/salida propias.

Ejemplos:
  python3 run_squeeze_backtest.py --csv ohlc_4h_formateado.csv
  python3 run_squeeze_backtest.py --symbol PF_XBTUSD --tf 4h --start 2022-01-01
  python3 run_squeeze_backtest.py --symbol PF_ETHUSD --tf 1h --exit_mode zero --trend_filter ema
  python3 run_squeeze_backtest.py --symbol PF_XBTUSD --tf 4h --replicate_bug false   # versión corregida
"""
from __future__ import annotations

import argparse
from dataclasses import fields
from pathlib import Path

import pandas as pd

from smc.backtest import BTConfig, run_signal_backtest, summarize
from smc.data import load_or_download
from smc.squeeze import SqueezeParams, generate_squeeze_signals


def _bool(s: str) -> bool:
    return s.lower() in ("1", "true", "yes", "si", "sí")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="CSV OHLC local")
    src.add_argument("--symbol", help="símbolo de Kraken Futures, p. ej. PF_XBTUSD")
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--costs", default="futures", choices=["futures", "spot", "zero"])
    ap.add_argument("--risk", type=float, default=0.01)
    ap.add_argument("--leverage", type=float, default=3.0)
    ap.add_argument("--max_hold", type=int, default=None, help="cerrar tras N velas")
    ap.add_argument("--out", default="results")
    for f in fields(SqueezeParams):
        conv = {"bool": _bool, "int": int, "float": float}.get(str(f.type).replace("<class '", "").rstrip("'>"), str)
        ap.add_argument(f"--{f.name}", type=conv, default=None, help=f"(defecto {f.default})")
    return ap


def main() -> None:
    a = build_parser().parse_args()
    out = Path(a.out)
    out.mkdir(exist_ok=True)
    df, tag = load_or_download(a.csv, a.symbol, a.tf, a.start, a.end)

    p = SqueezeParams(**{f.name: getattr(a, f.name) for f in fields(SqueezeParams)
                         if getattr(a, f.name) is not None})
    cfg = BTConfig(risk_per_trade=a.risk, max_leverage=a.leverage, costs=a.costs, max_hold_bars=a.max_hold)

    entries, exits = generate_squeeze_signals(df, p)
    trades, equity = run_signal_backtest(df, entries, exits, cfg)
    stats = summarize(trades, equity, df, cfg.initial_equity)

    print(f"\n=== Squeeze Momentum: {tag} | salida={p.exit_mode} | tendencia={p.trend_filter} "
          f"| bug={'sí' if p.replicate_bug else 'no'} | costes={a.costs} ===")
    print(f"señales de entrada: {len(entries)}")
    for k, v in stats.items():
        print(f"  {k:>18}: {v:,.3f}" if isinstance(v, float) else f"  {k:>18}: {v}")
    if len(trades):
        print("\n  salidas:", trades["reason"].value_counts().to_dict())
        print(f"  MFE mediano (R): {trades['mfe_r'].median():.2f}  "
              f"| % trades que llegaron a +1R: {100 * (trades['mfe_r'] >= 1).mean():.0f}%")
        for side, name in ((1, "largos"), (-1, "cortos")):
            t_ = trades[trades["side"] == side]
            if len(t_):
                print(f"  {name:>7}: {len(t_):>4} ops | R medio {t_['r'].mean():+.3f} | R total {t_['r'].sum():+.1f}")
        by_year = trades.groupby(pd.to_datetime(trades["entry_time"]).dt.year)["r"].agg(["count", "mean", "sum"])
        print("  por año:  " + "  ".join(f"{y}: {int(r['count'])} ops {r['sum']:+.1f}R" for y, r in by_year.iterrows()))
        total_r = trades["r"].sum()
        top5 = trades["r"].nlargest(5).sum()
        print(f"  R total {total_r:+.1f} | las 5 mejores aportan {top5:+.1f}R | sin ellas: {total_r - top5:+.1f}R")
        trades.to_csv(out / f"squeeze_trades_{tag}_{a.costs}.csv", index=False)
    equity.to_csv(out / f"squeeze_equity_{tag}_{a.costs}.csv")
    print(f"\nResultados guardados en {out}/")


if __name__ == "__main__":
    main()
