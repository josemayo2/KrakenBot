"""Reproducción vela a vela (el equivalente a "tv replay" + "tv stream" sin TradingView).

Solo se usan velas hasta --end: el indicador no ve nada del futuro.

    # 1) Flujo de paquetes JSON, uno por vela cerrada (como "tv stream tables")
    python -m ai_lens.replay --start 2026-11-01 --end 2026-11-18 --stream > packets.jsonl

    # 2) Modo ciego para un agente LLM: solo los paquetes de setup, sin resultados
    python -m ai_lens.replay --start 2026-11-01 --end 2026-11-18 --blind > setups.jsonl
    #    ...Claude decide con AGENT_PROMPT.md y escribe decisiones.json:
    #    [{"asset":"BTC","time":"2026-11-03T12:00:00Z","decision":"LONG"}, ...]

    # 3) Evaluar esas decisiones (o, sin --decisions, las del agente de reglas)
    python -m ai_lens.replay --start 2026-11-01 --end 2026-11-18 --decisions decisiones.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import pandas as pd

from . import agent, backtest, indicator


def _setup_view(p: dict) -> dict:
    """Paquete de setup sin el bloque de seguimiento de operaciones pasadas."""
    q = dict(p)
    q.pop("last_setup", None)
    return q


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--assets", nargs="+", default=["BTC", "ETH", "SOL"])
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--htf", default="240")
    ap.add_argument("--policy", default="full", choices=["full", "half_be"])
    ap.add_argument("--stream", action="store_true", help="imprimir un paquete JSON por vela")
    ap.add_argument("--delay", type=float, default=0.0, help="segundos entre paquetes con --stream")
    ap.add_argument("--blind", action="store_true", help="solo paquetes de setup, sin resultados")
    ap.add_argument("--decisions", help="JSON con decisiones de un agente LLM a evaluar")
    ap.add_argument("--no-levels", action="store_true")
    a = ap.parse_args()

    lenses = {x: backtest.build(x, a.tf, a.start, a.end, a.htf) for x in a.assets}

    if a.stream or a.blind:
        rows = []
        for x, (lens, i0) in lenses.items():
            for i in range(i0, len(lens.df)):
                if a.blind and lens.df["event"].iat[i] not in ("LONG_SETUP", "SHORT_SETUP"):
                    continue
                rows.append((lens.df["time"].iat[i], x, i))
        for _, x, i in sorted(rows):
            p = indicator.packet(lenses[x][0], i)
            print(json.dumps(_setup_view(p) if a.blind else p, ensure_ascii=False))
            sys.stdout.flush()
            if a.delay:
                time.sleep(a.delay)
        return

    rules = agent.Rules(check_levels=not a.no_levels)
    if a.decisions:
        with open(a.decisions) as f:
            given = {(d["asset"], d["time"]): d for d in json.load(f)}

        def choose_for(asset):
            def fn(p):
                d = given.get((asset, p["time"]), {"decision": "WAIT"})
                return {"decision": d["decision"], "confidence": d.get("confidence"),
                        "concerns": d.get("concerns"), "stop": d.get("stop"), "entry": None}
            return fn
        label = f"agente LLM ({a.decisions})"
    else:
        def choose_for(asset):
            return lambda p: agent.decide(p, rules)
        label = "agente de reglas"

    all_tr, all_dec = [], []
    for x, (lens, i0) in lenses.items():
        tr, dec = backtest.simulate(lens, i0, choose_for(x), a.policy, asset=x)
        all_tr += tr; all_dec += dec
        ind, _ = backtest.simulate(lens, i0, lambda p: {"decision": "LONG" if p["event"] == "LONG_SETUP" else "SHORT"},
                                   a.policy, asset=x)
        for t in ind:
            t["mode"] = "indicador"
        all_tr += ind
    tr = pd.DataFrame(all_tr)
    dec = pd.DataFrame(all_dec)
    if "mode" not in tr:
        tr["mode"] = None
    tr["mode"] = tr["mode"].fillna(label)

    pd.set_option("display.width", 200)
    print(f"\nReproducción {', '.join(a.assets)} {a.tf} | {a.start} -> {a.end} | htf={a.htf} | gestión={a.policy}\n")
    print("Decisiones en cada setup:")
    print(dec[["time", "asset", "event", "decision", "confidence"]].to_string(index=False))
    for mode, g in tr.groupby("mode", sort=False):
        print(f"\n== {mode} ==")
        cols = ["asset", "side", "entry_time", "entry", "stop", "exit_time", "exit_reason", "r_gross", "r_net"]
        print(g[cols].sort_values("entry_time").to_string(index=False))
        print(backtest.stats(g))


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:  # p. ej. al cortar la salida con | head
        pass
