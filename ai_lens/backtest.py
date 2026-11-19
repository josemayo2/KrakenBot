"""Backtest del indicador y del agente con datos de Kraken.

Ejemplos:
    python -m ai_lens.backtest                                  # BTC ETH SOL 4h, 2023 -> hoy
    python -m ai_lens.backtest --start 2026-11-01 --end 2026-11-18
    python -m ai_lens.backtest --policy half_be --htf D

Gestión de la posición (--policy):
    full     todo a objetivo 2 (+3R) o stop (-1R). Es lo que sigue el indicador.
    half_be  50 % en objetivo 1, stop a break-even, resto a objetivo 2: -1R / +0,75R / +2,25R.
Si una vela toca stop y objetivo a la vez, cuenta el stop (igual que el indicador).
Las comisiones y el deslizamiento se descuentan en R: (fee+slip) x 2 x entrada / riesgo.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from . import agent, data, indicator

RESULTS = Path(__file__).resolve().parent / "results"


@dataclass
class Costs:
    fee_bps: float = 5.0   # comisión taker de Kraken Futures por lado (0,05 %)
    slip_bps: float = 2.0  # deslizamiento estimado por lado


def build(asset: str, tf: str, start, end, htf: str = "240", threshold: int = 70, source: str = "futures"):
    """Indicador calculado solo con velas hasta `end` (nada del futuro)."""
    df = data.load(asset, tf, source=source, start="2022-01-01")
    end_ts = pd.Timestamp(end, tz="UTC") if pd.Timestamp(end).tzinfo is None else pd.Timestamp(end)
    sec = data.TF_SECONDS[tf]
    df = df[df["time"] + pd.Timedelta(seconds=sec) <= end_ts].reset_index(drop=True)
    P = indicator.Params(htf=htf, score_threshold=threshold)
    lens = indicator.compute(df, tf, P, symbol=f"KRAKEN:{data.kraken_symbol(asset, source)}")
    start_ts = pd.Timestamp(start, tz="UTC") if pd.Timestamp(start).tzinfo is None else pd.Timestamp(start)
    i0 = int((lens.df["time"] < start_ts).sum())
    return lens, i0


def simulate(lens: indicator.Lens, i0: int, choose: Callable[[dict], dict], policy: str = "full",
             costs: Costs = Costs(), asset: str = "") -> tuple[list[dict], list[dict]]:
    """Recorre las velas desde i0. En cada setup pregunta al agente `choose(paquete)`.

    Devuelve (operaciones, decisiones).
    """
    d = lens.df
    h, l, c = d["high"].to_numpy(), d["low"].to_numpy(), d["close"].to_numpy()
    t = d["time"] + pd.Timedelta(seconds=indicator.TF_CODE_SECONDS[lens.tf])  # hora de cierre de la vela
    ev = d["event"].to_numpy()
    P = lens.params
    trades, decisions = [], []
    pos = None

    def close(pos, i, reason, price=None):
        s = 1 if pos["side"] == "LONG" else -1
        if price is not None:  # cierre a mercado del resto de la posición
            pos["r_gross"] += pos["remaining"] * s * (price - pos["entry"]) / pos["risk"]
        cost = (costs.fee_bps + costs.slip_bps) * 2 / 1e4 * pos["entry"] / pos["risk"]
        pos.update(exit_time=t.iloc[i], exit_reason=reason, r_gross=round(pos["r_gross"], 3),
                   r_net=round(pos["r_gross"] - cost, 3), cost_r=round(cost, 3))
        trades.append(pos)

    for i in range(i0, len(d)):
        if pos is not None and i > pos["bar"]:
            s = 1 if pos["side"] == "LONG" else -1
            hit_stop = (l[i] <= pos["stop"]) if s == 1 else (h[i] >= pos["stop"])
            hit_t2 = (h[i] >= pos["t2"]) if s == 1 else (l[i] <= pos["t2"])
            hit_t1 = (h[i] >= pos["t1"]) if s == 1 else (l[i] <= pos["t1"])
            if hit_stop:
                pos["r_gross"] += pos["remaining"] * s * (pos["stop"] - pos["entry"]) / pos["risk"]
                pos["remaining"] = 0
                close(pos, i, "STOPPED_AFTER_T1" if pos["t1_hit"] else "STOPPED"); pos = None
            elif hit_t2:
                if policy == "half_be" and not pos["t1_hit"]:
                    pos["r_gross"] += 0.5 * P.t1_r; pos["remaining"] = 0.5
                pos["r_gross"] += pos["remaining"] * P.t2_r
                pos["remaining"] = 0
                close(pos, i, "TARGET_2_HIT"); pos = None
            elif hit_t1 and not pos["t1_hit"]:
                pos["t1_hit"] = True
                if policy == "half_be":
                    pos["r_gross"] += 0.5 * P.t1_r; pos["remaining"] = 0.5
                    pos["stop"] = pos["entry"]  # break-even desde la vela siguiente
        if ev[i] in ("LONG_SETUP", "SHORT_SETUP"):
            pkt = indicator.packet(lens, i)
            dec = choose(pkt)
            decisions.append({"asset": asset, "time": pkt["time"], "event": ev[i], **{k: dec.get(k) for k in
                              ("decision", "confidence", "concerns")}})
            side = dec.get("decision")
            if side in ("LONG", "SHORT"):
                if pos is not None and pos["side"] == side:
                    decisions[-1]["note"] = "ya hay posición en esa dirección"
                    continue
                if pos is not None:
                    close(pos, i, "REVERSED", price=c[i]); pos = None
                entry = float(dec.get("entry") or c[i])
                stop = float(dec.get("stop") or d["t_stop"].iat[i])
                risk = abs(entry - stop)
                sgn = 1 if side == "LONG" else -1
                pos = dict(asset=asset, side=side, bar=i, entry_time=t.iloc[i], entry=entry, stop=stop, risk=risk,
                           t1=entry + sgn * P.t1_r * risk, t2=entry + sgn * P.t2_r * risk,
                           t1_hit=False, remaining=1.0, r_gross=0.0, confidence=dec.get("confidence"))
    if pos is not None:
        close(pos, len(d) - 1, "OPEN", price=c[-1])
    return trades, decisions


def stats(tr: pd.DataFrame) -> dict:
    if tr.empty:
        return dict(ops=0, cerradas=0, aciertos="-", R_total=0.0, R_media=0.0, max_DD_R=0.0, PF="-")
    closed = tr[tr["exit_reason"] != "OPEN"]
    r = closed.sort_values("exit_time")["r_net"]
    eq = r.cumsum()
    dd = float((eq.cummax().clip(lower=0) - eq).max()) if len(eq) else 0.0
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    return dict(ops=len(tr), cerradas=len(closed),
                aciertos=f"{(r > 0).mean() * 100:.0f}%" if len(r) else "-",
                R_total=round(float(r.sum()), 2), R_media=round(float(r.mean()), 3) if len(r) else 0.0,
                max_DD_R=round(dd, 2), PF=round(float(gains / losses), 2) if losses > 0 else "inf")


def run(assets, tf, start, end, htf, policy, costs, threshold, rules: agent.Rules):
    modes = {
        "indicador (todas las señales)": lambda p: {"decision": "LONG" if p["event"] == "LONG_SETUP" else "SHORT"},
        "agente de reglas": lambda p: agent.decide(p, rules),
    }
    out = {}
    for name, fn in modes.items():
        all_tr, all_dec = [], []
        for a in assets:
            lens, i0 = build(a, tf, start, end, htf, threshold)
            tr, dec = simulate(lens, i0, fn, policy, costs, asset=a)
            all_tr += tr; all_dec += dec
        out[name] = (pd.DataFrame(all_tr), pd.DataFrame(all_dec))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--assets", nargs="+", default=["BTC", "ETH", "SOL"])
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M"))
    ap.add_argument("--htf", default="240", help="marco superior (240 = igual al gráfico de 4h -> neutro, D, W)")
    ap.add_argument("--policy", default="full", choices=["full", "half_be"])
    ap.add_argument("--threshold", type=int, default=70)
    ap.add_argument("--fee-bps", type=float, default=5.0)
    ap.add_argument("--slip-bps", type=float, default=2.0)
    ap.add_argument("--no-levels", action="store_true", help="el agente no hace SKIP por niveles en el camino al T1")
    ap.add_argument("--save", action="store_true", help="guardar operaciones en ai_lens/results/")
    a = ap.parse_args()

    rules = agent.Rules(check_levels=not a.no_levels)
    res = run(a.assets, a.tf, a.start, a.end, a.htf, a.policy, Costs(a.fee_bps, a.slip_bps), a.threshold, rules)
    print(f"\n{', '.join(a.assets)} {a.tf} | {a.start} -> {a.end} | htf={a.htf} | gestión={a.policy} | "
          f"costes {a.fee_bps}+{a.slip_bps} bps/lado\n")
    for name, (tr, dec) in res.items():
        rows = {asset: stats(tr[tr["asset"] == asset] if not tr.empty else tr) for asset in a.assets}
        rows["TOTAL"] = stats(tr)
        print(f"== {name} ==")
        print(pd.DataFrame(rows).T.to_string())
        if not tr.empty and len(tr["exit_time"].dt.year.unique()) > 1:
            closed = tr[tr["exit_reason"] != "OPEN"]
            print("R neto por año:", closed.groupby(closed["exit_time"].dt.year)["r_net"].sum().round(1).to_dict())
        if not dec.empty and name.startswith("agente"):
            print("decisiones:", dec["decision"].value_counts().to_dict())
        print()
        if a.save:
            RESULTS.mkdir(exist_ok=True)
            slug = name.split()[0]
            tr.to_csv(RESULTS / f"trades_{slug}_{a.policy}_{a.htf}.csv", index=False)
            dec.to_csv(RESULTS / f"decisiones_{slug}_{a.policy}_{a.htf}.csv", index=False)


if __name__ == "__main__":
    main()
