#!/usr/bin/env python3
"""Simula el BOT REAL (live.squeeze_bot.run_once) sobre el histórico, vela a vela y con
todos los mercados a la vez: misma lógica, mismos topes de riesgo y misma pausa por
drawdown que en el servidor. Responde a: "¿cómo habría evolucionado mi saldo?".

Usa los CSV de data/ (los descarga de Kraken si faltan). Por velocidad, las señales se
calculan una vez sobre todo el histórico y se recortan en cada vela; es equivalente
porque los indicadores no miran al futuro (lo comprueban los tests).

Uso:
  python3 simulate_bot.py                       # configuración del .env de ejemplo
  python3 simulate_bot.py --capital 1000 --cap 1000 --halt 0.5
  python3 simulate_bot.py --compare             # compara 3 configuraciones
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

import live.squeeze_bot as bot
from live.brokers import PaperBroker
from live.config import LiveConfig
from smc.data import load_or_download
from smc.squeeze import generate_squeeze_signals

DEFAULT_SYMBOLS = ["PF_XBTUSD", "PF_ETHUSD", "PF_SOLUSD", "PF_ADAUSD", "PF_LINKUSD", "PF_DOGEUSD"]


class Collector(bot.Notifier):
    def __init__(self):
        super().__init__()
        self.msgs: list[tuple[pd.Timestamp, str]] = []
        self.now = None

    def send(self, msg):
        self.msgs.append((self.now, msg))


def simulate(data: dict, cfg: LiveConfig, capital: float, workdir: Path) -> dict:
    cfg.state_path = str(workdir / "state.json")
    cfg.trades_log = str(workdir / "trades.csv")
    broker = PaperBroker(str(workdir / "paper.json"), initial_equity=capital)

    pre = {s: generate_squeeze_signals(df, cfg.params) for s, df in data.items()}

    def cached_signals(df, params=None):
        e, x = pre[df.attrs["sym"]]
        n = len(df)
        return e[e["bar"] < n], x.iloc[:n]

    original = bot.generate_squeeze_signals
    bot.generate_squeeze_signals = cached_signals
    note = Collector()
    times = sorted(set().union(*[set(df.index) for df in data.values()]))
    pos = {s: 0 for s in data}
    eq_curve, n_open = [], []
    try:
        for t in times:
            for s, df in data.items():  # cuántas velas de cada mercado hay cerradas en t
                while pos[s] < len(df) and df.index[pos[s]] <= t:
                    pos[s] += 1
            views = {s: data[s].iloc[:pos[s]] for s in data}
            note.now = t
            bot.run_once(cfg, broker, lambda s: views[s], note, now=t + pd.Timedelta(hours=4))
            marks = {s: float(v["close"].iloc[-1]) for s, v in views.items() if len(v)}
            eq_curve.append((t, broker.equity(marks)))
            n_open.append(len(broker.s["positions"]))
    finally:
        bot.generate_squeeze_signals = original

    eq = pd.Series(dict(eq_curve)).sort_index()
    tl = pd.read_csv(cfg.trades_log) if Path(cfg.trades_log).exists() else pd.DataFrame()
    return {"equity": eq, "trades": tl, "msgs": note.msgs, "max_open": max(n_open or [0]),
            "avg_open": float(np.mean(n_open or [0]))}


def report(name: str, res: dict, capital: float) -> dict:
    eq, tl = res["equity"], res["trades"]
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    final = eq.iloc[-1]
    dd = eq / eq.cummax() - 1
    entries = tl[tl["action"] == "entry"] if len(tl) else tl
    closes = tl[tl["action"].isin(["exit", "stop"])] if len(tl) else tl
    halts = [t for t, m in res["msgs"] if "Drawdown" in m]
    skipped = sum(1 for _, m in res["msgs"] if "ignorada" in m or "menor que el mínimo" in m)

    # P&L por operación: emparejar cada entrada con su cierre
    pnl = []
    if len(tl):
        open_ = {}
        for r in tl.itertuples():
            if r.action == "entry":
                open_[r.symbol] = r
            elif r.symbol in open_:
                e = open_.pop(r.symbol)
                pnl.append({"symbol": r.symbol, "year": pd.Timestamp(r.time).year,
                            "pnl": e.side * e.size * (r.price - e.price)})
    pnl = pd.DataFrame(pnl)

    print(f"\n=== {name} ===")
    print(f"  Periodo: {eq.index[0]:%Y-%m-%d} → {eq.index[-1]:%Y-%m-%d} ({years:.1f} años)")
    print(f"  Capital: {capital:,.0f} $ → {final:,.0f} $  ({100 * (final / capital - 1):+.1f}%, "
          f"{100 * ((final / capital) ** (1 / years) - 1):+.1f}% anual)")
    print(f"  Peor caída desde máximo: {100 * dd.min():.1f}%  (el {dd.idxmin():%Y-%m-%d})")
    print(f"  Operaciones: {len(entries)}  | cerradas con beneficio: "
          f"{100 * (pnl['pnl'] > 0).mean() if len(pnl) else 0:.0f}%  | "
          f"posiciones abiertas a la vez: media {res['avg_open']:.1f}, máx {res['max_open']}")
    print(f"  Señales no ejecutadas (pausa o topes): {skipped}")
    print(f"  Pausa por drawdown: {'SALTÓ el ' + f'{halts[0]:%Y-%m-%d}' + ' (desde ahí no abrió más)' if halts else 'no saltó nunca'}")
    if len(pnl):
        yr = eq.resample("YE").last()
        start = pd.concat([pd.Series({eq.index[0]: eq.iloc[0]}), yr]).shift(1).dropna()
        print("  Por año:   " + "  ".join(f"{d.year}: {100 * (v / start[d] - 1):+.0f}%"
                                         for d, v in yr.items() if d in start.index))
        by_sym = pnl.groupby("symbol")["pnl"].sum().sort_values(ascending=False)
        print("  Por mercado ($): " + "  ".join(f"{s.replace('PF_', '').replace('USD', '')} {v:+.0f}"
                                               for s, v in by_sym.items()))
        worst = pnl["pnl"].rolling(10).sum().min()
        print(f"  Peor racha de 10 operaciones seguidas: {worst:+.0f} $")
    return {"final": final, "dd": dd.min()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--capital", type=float, default=1000.0)
    ap.add_argument("--cap", type=float, default=1000.0, help="BOT_CAPITAL_CAP (0 = sin tope, interés compuesto)")
    ap.add_argument("--halt", type=float, default=0.5, help="pausa por drawdown (1 = desactivada)")
    ap.add_argument("--risk", type=float, default=0.01)
    ap.add_argument("--max_notional", type=float, default=500.0)
    ap.add_argument("--max_total", type=float, default=2500.0)
    ap.add_argument("--compare", action="store_true", help="compara la config del bot con variantes")
    ap.add_argument("--data-dir", default="data")
    a = ap.parse_args()

    data = {}
    for s in a.symbols:
        df = load_or_download(None, s, "4h", a.start, None, a.data_dir)[0]
        df.attrs["sym"] = s
        data[s] = df
    print(f"Mercados: {', '.join(a.symbols)} | velas de 4h | simulando el bot vela a vela...")

    def cfg_for(cap, halt, max_notional=a.max_notional, max_total=a.max_total):
        c = LiveConfig(mode="paper", symbols=a.symbols, risk_pct=a.risk,
                       max_notional_per_trade=max_notional, max_total_notional=max_total,
                       max_drawdown_halt=halt, capital_cap=cap)
        return c

    runs = [("TU CONFIGURACIÓN (la del bot)", cfg_for(a.cap, a.halt))]
    if a.compare:
        runs += [("Igual pero SIN pausa por drawdown", cfg_for(a.cap, 1.0)),
                 ("Sin tope de capital (reinvierte ganancias) y sin pausa",
                  cfg_for(0.0, 1.0, max_notional=1e12, max_total=1e12))]
    for name, cfg in runs:
        with tempfile.TemporaryDirectory() as d:
            report(name, simulate(data, cfg, a.capital, Path(d)), a.capital)
    print("\nNo incluye funding de perpetuos. Resultados pasados no garantizan los futuros.")


if __name__ == "__main__":
    main()
