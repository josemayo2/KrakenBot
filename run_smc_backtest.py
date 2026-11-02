#!/usr/bin/env python3
"""Backtest de la estrategia SMC (barrido de liquidez + MSS + FVG).

Ejemplos:
  # con un CSV local
  python run_smc_backtest.py --csv ohlc_4h_formateado.csv

  # descargando de Kraken Futures (recomendado, histórico largo)
  python run_smc_backtest.py --symbol PF_XBTUSD --tf 1h --start 2022-01-01
  python run_smc_backtest.py --symbol PF_ETHUSD --tf 15m --start 2024-01-01 --costs futures

  # comparar con comisiones de spot
  python run_smc_backtest.py --csv ohlc_4h_formateado.csv --costs spot
"""
from __future__ import annotations

import argparse
from dataclasses import fields
from pathlib import Path

from smc import BTConfig, SMCParams, generate_setups, run_backtest, summarize
from smc.data import load_or_download


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="CSV OHLC local")
    src.add_argument("--symbol", help="símbolo de Kraken Futures, p. ej. PF_XBTUSD")
    ap.add_argument("--tf", default="1h", help="resolución para --symbol (1m,5m,15m,30m,1h,4h,12h,1d)")
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--costs", default="futures", choices=["futures", "spot", "zero"])
    ap.add_argument("--risk", type=float, default=0.01, help="riesgo por operación (0.01 = 1%%)")
    ap.add_argument("--leverage", type=float, default=3.0)
    ap.add_argument("--out", default="results", help="carpeta de salida")
    # todos los parámetros de la estrategia como --nombre valor
    for f in fields(SMCParams):
        kw = {"default": None, "help": f"(defecto {f.default})"}
        if f.type in ("bool", bool):
            kw["type"] = lambda s: s.lower() in ("1", "true", "yes", "si", "sí")
        elif f.type in ("int", int):
            kw["type"] = int
        elif f.type in ("float", float):
            kw["type"] = float
        ap.add_argument(f"--{f.name}", **kw)
    return ap


def main() -> None:
    a = build_parser().parse_args()
    out = Path(a.out)
    out.mkdir(exist_ok=True)

    df, tag = load_or_download(a.csv, a.symbol, a.tf, a.start, a.end)

    p = SMCParams(**{f.name: getattr(a, f.name) for f in fields(SMCParams)
                     if getattr(a, f.name) is not None})
    cfg = BTConfig(risk_per_trade=a.risk, max_leverage=a.leverage, costs=a.costs)

    setups = generate_setups(df, p)
    trades, equity = run_backtest(df, setups, cfg)
    stats = summarize(trades, equity, df, cfg.initial_equity)

    print(f"\n=== SMC backtest: {tag} | costes={a.costs} | riesgo={a.risk:.1%} ===")
    print(f"setups generados: {len(setups)}")
    for k, v in stats.items():
        print(f"  {k:>18}: {v:,.3f}" if isinstance(v, float) else f"  {k:>18}: {v}")
    if len(trades):
        print("\n  salidas:", trades["reason"].value_counts().to_dict())
        trades.to_csv(out / f"smc_trades_{tag}_{a.costs}.csv", index=False)
    equity.to_csv(out / f"smc_equity_{tag}_{a.costs}.csv")
    print(f"\nResultados guardados en {out}/")


if __name__ == "__main__":
    main()
