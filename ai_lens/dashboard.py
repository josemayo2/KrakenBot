"""Exporta los datos del panel de seguimiento (dashboard) a JSON.

    python -m ai_lens.dashboard paper      -> ai_lens/paper/dashboard.json    (cada 4 h, tras actualizar el paper)
    python -m ai_lens.dashboard backtest   -> ai_lens/results/backtest.json   (fijo; tarda ~2 min)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from . import agent, backtest, paper, robustez

RESULTS = Path(__file__).resolve().parent / "results"


def _iso(t) -> str | None:
    if t is None or (isinstance(t, float) and pd.isna(t)) or pd.isna(t):
        return None
    return pd.Timestamp(t).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pct(x):
    if isinstance(x, str):
        return float(x.rstrip("%")) if x.rstrip("%").replace(".", "", 1).isdigit() else None
    return x


def _num(x):
    try:
        v = float(x)
        return None if pd.isna(v) or v in (float("inf"), float("-inf")) else round(v, 4)
    except (TypeError, ValueError):
        return None


def paper_payload() -> dict:
    cfg = paper._config()
    state = paper._read_json("estado.json", {"ultima_vela": {}})
    lenses = paper._lenses(cfg)
    tr, summary, dec = paper.evaluate(cfg, lenses)
    now = pd.Timestamp.now(tz="UTC")
    inicio = paper._ts(cfg["inicio"])
    ultima = min(state.get("ultima_vela", {}).values() or [cfg["inicio"]])

    resumen = []
    for name, s in summary.items():
        n_open = int((tr[(tr["estrategia"] == name) & (tr["exit_reason"] == "OPEN")].shape[0])) if not tr.empty else 0
        decs = s.get("decisiones", {})
        resumen.append(dict(
            estrategia=name, descripcion=paper.STRATEGIES[name][1],
            capital=_num(s["capital"]), rentab_pct=_num((s["capital"] / cfg["capital"] - 1) * 100),
            abierto_usd=_num(s["abierto"]), cerradas=int(s["cerradas"]), abiertas=n_open,
            aciertos_pct=_pct(s["aciertos"]), r_neto=_num(s["R_total"]), r_op=_num(s["R_media"]),
            max_dd_pct=_num(-s["max_dd_pct"]), pf=_num(s["PF"]),
            setups=int(sum(decs.values())), operadas=int(decs.get("LONG", 0) + decs.get("SHORT", 0))))

    equity = []
    for name in paper.STRATEGIES:
        equity.append(dict(fecha=_iso(inicio), estrategia=name, capital=cfg["capital"]))
        if not tr.empty:
            cl = tr[(tr["estrategia"] == name) & (tr["exit_reason"] != "OPEN")].sort_values("exit_time")
            equity += [dict(fecha=_iso(t.exit_time), estrategia=name, capital=_num(t.capital_tras))
                       for t in cl.itertuples()]
        equity.append(dict(fecha=_iso(now), estrategia=name, capital=_num(summary[name]["capital"])))

    ops = []
    if not tr.empty:
        for t in tr.sort_values("entry_time", ascending=False).itertuples():
            abierta = t.exit_reason == "OPEN"
            ops.append(dict(id=f"{t.estrategia}|{t.asset}|{_iso(t.entry_time)}", estrategia=t.estrategia,
                            activo=t.asset, lado=t.side, entrada_fecha=_iso(t.entry_time), entrada=_num(t.entry),
                            stop=_num(t.stop), objetivo=_num(t.t2), salida_fecha=None if abierta else _iso(t.exit_time),
                            motivo="ABIERTA" if abierta else t.exit_reason, r_neto=_num(t.r_net),
                            pnl_usd=_num(t.pnl_usd), estado="abierta" if abierta else "cerrada"))

    setups = []
    if not dec.empty:
        cl = {(d["activo"], d["vela"]): d for d in paper._read_json("decisiones_claude.json", [])}
        for (asset, vela), g in dec.groupby(["asset", "time"]):
            by = dict(zip(g["estrategia"], g["decision"]))
            c = cl.get((asset, vela), {})
            setups.append(dict(id=f"{asset}|{vela}", vela=vela, activo=asset, evento=g["event"].iloc[0],
                               indicador=by.get("indicador", "—"), reglas=by.get("reglas", "—"),
                               reglas_htfD=by.get("reglas_htfD", "—"), claude=by.get("claude", "—"),
                               claude_confianza=c.get("confianza"),
                               claude_dudas="; ".join(c.get("dudas") or []) or None))
        setups.sort(key=lambda r: r["vela"], reverse=True)

    estado = [dict(inicio=cfg["inicio"], actualizado=_iso(now), ultima_vela=ultima,
                   dias=round((now - inicio).total_seconds() / 86400, 1), capital_inicial=cfg["capital"],
                   riesgo_pct=cfg["riesgo_pct"], activos=", ".join(cfg["activos"]), tf=cfg["tf"],
                   costes_bps_lado=cfg["comision_bps"] + cfg["desliz_bps"])]
    return dict(estado=estado, resumen=resumen, equity=equity, operaciones=ops, setups=setups)


def backtest_payload(start="2022-06-01", end="2026-11-18 00:00", sims=300) -> dict:
    out = robustez.compute(start, end, sims)
    res = {}
    for htf, label in [("240", ""), ("D", "_htfD")]:
        r = backtest.run(robustez.ASSETS, "4h", start, end, htf, "full", backtest.Costs(5.0, 2.0), 70, agent.Rules())
        for mode, (tr, _) in r.items():
            key = ("indicador" if mode.startswith("indicador") else "reglas") + label
            if key == "indicador_htfD":
                continue
            closed = tr[tr["exit_reason"] != "OPEN"]
            res[key] = robustez.summarize([(t.asset, t.entry_time, t.exit_time, 1 if t.side == "LONG" else -1, t.r_net)
                                           for t in closed.itertuples()])
    desc = {"indicador": "Todas las señales del indicador", "reglas": "Agente de reglas (AGENT_PROMPT)",
            "reglas_htfD": "Agente de reglas con sesgo diario"}
    out["estrategias"] = [dict(estrategia=k, descripcion=desc[k], **v) for k, v in res.items()]
    return out


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "paper"
    if what == "paper":
        f = paper.PAPER_DIR / "dashboard.json"
        f.write_text(json.dumps(paper_payload(), ensure_ascii=False))
    elif what == "backtest":
        RESULTS.mkdir(exist_ok=True)
        f = RESULTS / "backtest.json"
        f.write_text(json.dumps(backtest_payload(), ensure_ascii=False))
    else:
        sys.exit("uso: python -m ai_lens.dashboard [paper|backtest]")
    print(f)


if __name__ == "__main__":
    main()
