"""Agente de reglas: traducción determinista de AGENT_PROMPT.md.

Un LLM (Claude) puede leer el paquete y razonar con matices; esta versión aplica las
mismas reglas de forma fija para poder hacer backtests largos y reproducibles.

Reglas (de AGENT_PROMPT.md):
  - LONG/SHORT solo en un evento LONG_SETUP / SHORT_SETUP.
  - SKIP: régimen HIGH_VOLATILITY, va contra htf_bias (si mis reglas lo prohíben),
          R:R en objetivo 2 por debajo del mínimo, o un nivel bloquea el camino al objetivo 1.
  - WAIT: falta UNA condición (la vela no acompaña o la estructura va en contra). Si faltan
          dos o más, SKIP.
  - Tamaño = (cuenta x riesgo %) / |entrada - stop|, redondeado hacia abajo.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Rules:
    account: float = 1000.0
    risk_pct: float = 1.0
    allow_long: bool = True
    allow_short: bool = True
    min_rr_t2: float = 2.0
    respect_htf: bool = True
    check_levels: bool = True  # SKIP si un nivel está entre la entrada y el objetivo 1
    size_decimals: int = 4


def _levels_in_path(p: dict, side: str, entry: float, t1: float) -> list[str]:
    lv = {"prev_day_high": p["levels"]["prev_day_high"], "prev_day_low": p["levels"]["prev_day_low"],
          "vwap": p["levels"]["vwap"], "last_swing_high": p["structure"]["last_swing_high"],
          "last_swing_low": p["structure"]["last_swing_low"]}
    out = []
    for name, val in lv.items():
        if val is None:
            continue
        if side == "LONG" and entry < val < t1:
            out.append(f"{name} {val} entre la entrada y el objetivo 1")
        if side == "SHORT" and t1 < val < entry:
            out.append(f"{name} {val} entre la entrada y el objetivo 1")
    return out


def decide(p: dict, rules: Rules | None = None) -> dict:
    R = rules or Rules()
    if p.get("schema") != "ai-agent-lens/v1":
        return {"decision": "IGNORE", "reasons": ["schema desconocido"]}
    ev = p["event"]
    side = "LONG" if ev == "LONG_SETUP" else "SHORT" if ev == "SHORT_SETUP" else None
    base = {"symbol": p["symbol"], "timeframe": p["timeframe"], "packet_time": p["time"]}
    if side is None:
        lead = "long" if p["scores"]["long"] >= p["scores"]["short"] else "short"
        return {**base, "decision": "WAIT", "confidence": 0, "entry": None, "stop": None, "target_1": None,
                "target_2": None, "reasons": [f"Sin setup. Lidera {lead} con {p['scores'][lead]} de {p['scores']['threshold']}"],
                "concerns": [], "next_check": "próxima vela cerrada"}

    t = p["trade"]
    entry, stop, t1, t2 = t["entry"], t["stop"], t["target_1"], t["target_2"]
    risk = abs(entry - stop)
    rr2 = abs(t2 - entry) / risk if risk else 0.0
    c = p["candle"]
    fac = p["scores"]["long_factors" if side == "LONG" else "short_factors"]
    score = p["scores"]["long" if side == "LONG" else "short"]
    htf = p["trend"]["htf_bias"]
    struct = p["structure"]["bias"]

    skip, wait, concerns = [], [], []
    if p["regime"] == "HIGH_VOLATILITY":
        skip.append("régimen HIGH_VOLATILITY")
    if (side == "LONG" and not R.allow_long) or (side == "SHORT" and not R.allow_short):
        skip.append(f"{side} no permitido en mis reglas")
    if R.respect_htf and ((side == "LONG" and htf == "DOWN") or (side == "SHORT" and htf == "UP")):
        skip.append(f"va contra el sesgo del marco superior ({htf})")
    if rr2 < R.min_rr_t2:
        skip.append(f"R:R en objetivo 2 = {rr2:.2f} < {R.min_rr_t2}")
    if R.check_levels:
        skip += _levels_in_path(p, side, entry, t1)

    if side == "LONG" and not (c["direction"] == "bull" and c["close_position_pct"] >= 50):
        wait.append(f"la vela no acompaña ({c['direction']}, cierra al {c['close_position_pct']}% del rango)")
    if side == "SHORT" and not (c["direction"] == "bear" and c["close_position_pct"] <= 50):
        wait.append(f"la vela no acompaña ({c['direction']}, cierra al {c['close_position_pct']}% del rango)")
    if (side == "LONG" and struct == "BEARISH") or (side == "SHORT" and struct == "BULLISH"):
        wait.append(f"estructura {struct} en contra")

    weak = [k for k, v in fac.items() if v < 12]
    if weak:
        concerns.append("factores débiles: " + ", ".join(f"{k} {fac[k]}/20" for k in weak))
    if htf == "N/A":
        concerns.append("sin marco superior (htf_bias N/A): el factor htf vale 10 neutro")

    if skip:
        decision = "SKIP"
    elif len(wait) >= 2:
        decision = "SKIP"
    elif len(wait) == 1:
        decision = "WAIT"
    else:
        decision = side

    conf = score - 8 * len(weak) - 10 * len(wait)
    if weak or skip:
        conf = min(conf, 80)
    conf = max(0, min(100, conf))
    risk_amount = R.account * R.risk_pct / 100
    size = math.floor(risk_amount / risk * 10 ** R.size_decimals) / 10 ** R.size_decimals if risk else 0.0
    reasons = [f"{ev} con puntuación {score}/{p['scores']['threshold']}",
               f"régimen {p['regime']}, estructura {struct}",
               f"vela {c['pattern'] if c['pattern'] != 'NONE' else c['direction']}, cierre al {c['close_position_pct']}% del rango"]
    return {
        **base, "decision": decision, "confidence": int(conf),
        "entry": entry, "stop": stop, "target_1": t1, "target_2": t2,
        "risk_reward_t2": round(rr2, 2),
        "position_size_units": size if decision in ("LONG", "SHORT") else 0,
        "risk_amount": risk_amount if decision in ("LONG", "SHORT") else 0,
        "candle_read": f"{c['direction']} body {c['body_pct']}%, rango {c['range_atr']} ATR, patrón {c['pattern']}",
        "reasons": reasons[:3],
        "concerns": (skip + wait + concerns)[:3],
        "invalidation": f"cierre {'por debajo' if side == 'LONG' else 'por encima'} de {stop}",
        "why_this_could_fail": "; ".join((weak and [f"{', '.join(weak)} débil"]) or ["confluencia completa, pero el stop de 1,5 ATR puede saltar con ruido"]),
        "next_check": "próxima vela cerrada",
    }
