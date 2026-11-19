"""Modo en vivo: a cada cierre de vela calcula el paquete y la decisión del agente de reglas.

Equivale a "tv stream" sin TradingView: lee las velas de Kraken, no envía órdenes.

    python -m ai_lens.live                       # BTC ETH SOL 4h, se queda esperando cada cierre
    python -m ai_lens.live --once                # una sola pasada con la última vela cerrada

Escribe:
    ai_lens/packets/latest_<ACTIVO>.json   último paquete de cada activo
    ai_lens/packets/history.jsonl          todos los paquetes (uno por línea)

Para que Claude Code decida con criterio propio, abre Claude Code en el repo y pídele:
    "Usa ai_lens/AGENT_PROMPT.md como instrucciones. Lee ai_lens/packets/latest_BTC.json
     y dame la señal de esta vela."

Telegram (opcional): ver notify.py. Avisa en cada LONG_SETUP / SHORT_SETUP.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd

from . import agent, data, indicator, notify

OUT = Path(__file__).resolve().parent / "packets"
WARMUP_BARS = 1500  # suficiente para EMA 200 y percentil de ATR


def step(assets, tf, htf, rules):
    OUT.mkdir(exist_ok=True)
    now = pd.Timestamp.now(tz="UTC")
    sec = data.TF_SECONDS[tf]
    for a in assets:
        sym = data.kraken_symbol(a)
        df = data.fetch_futures(sym, tf, now - pd.Timedelta(seconds=WARMUP_BARS * sec), now)
        lens = indicator.compute(df, tf, indicator.Params(htf=htf), symbol=f"KRAKEN:{sym}")
        p = indicator.packet(lens, len(lens.df) - 1)
        d = agent.decide(p, rules)
        (OUT / f"latest_{a}.json").write_text(json.dumps(p, indent=1, ensure_ascii=False))
        with open(OUT / "history.jsonl", "a") as f:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
        print(f"{p['time']} {a:4} {p['state_code']}  -> {d['decision']}"
              + (f" ({'; '.join(d['concerns'])})" if d.get("concerns") else ""))
        if p["event"] in ("LONG_SETUP", "SHORT_SETUP"):
            t = p["trade"]
            notify.send(f"AI Lens {a} {tf}: {p['event']} | agente: {d['decision']} (conf {d.get('confidence')})\n"
                     f"entrada {t['entry']}  stop {t['stop']}  T1 {t['target_1']}  T2 {t['target_2']}\n"
                     f"tamaño {d.get('position_size_units')}  dudas: {'; '.join(d.get('concerns') or [])}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--assets", nargs="+", default=["BTC", "ETH", "SOL"])
    ap.add_argument("--tf", default="4h", choices=list(data.TF_SECONDS))
    ap.add_argument("--htf", default="240")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    rules = agent.Rules()
    sec = data.TF_SECONDS[a.tf]
    while True:
        step(a.assets, a.tf, a.htf, rules)
        if a.once:
            break
        wait = sec - time.time() % sec + 20  # 20 s después del cierre para que Kraken publique la vela
        print(f"próximo cierre en {wait / 60:.0f} min")
        time.sleep(wait)


if __name__ == "__main__":
    main()
