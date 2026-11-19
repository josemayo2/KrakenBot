"""Paper trading de AI Lens: cartera simulada, sin claves y sin órdenes reales.

Compara en paralelo, desde el día en que lo arrancas (solo hacia delante):
    indicador     todas las señales del indicador
    reglas        agente de reglas (AGENT_PROMPT en código)
    reglas_htfD   agente de reglas con sesgo del marco diario
    claude        decisiones de Claude con AGENT_PROMPT (si las registras a tiempo)

Uso:
    python -m ai_lens.paper init                    # arranca hoy: 1000 USD, 0,5 % de riesgo por operación
    python -m ai_lens.paper                         # actualiza (ponlo en cron cada hora; recupera velas perdidas)
    python -m ai_lens.paper informe                 # recalcula e imprime el informe
    python -m ai_lens.paper pendientes              # setups que esperan la decisión de Claude
    python -m ai_lens.paper decidir --activo SOL --vela 2026-11-20T04:00:00Z --decision SHORT \
        --confianza 60 --dudas "RSI 27" "nivel 69.8"

Seguridad:
  - Solo usa la API pública de Kraken (precios). No lee claves ni tiene código de órdenes.
  - Solo velas cerradas: el indicador no ve el futuro, y recalcular da siempre el mismo resultado.
  - La decisión de Claude solo cuenta si se registra antes de que cierre la vela siguiente
    al setup; si llega tarde se guarda como "TARDE" y no opera. Entra al precio real del
    momento en que decide (ask para LONG, bid para SHORT), no al cierre de la vela.
  - Bloqueo de archivo para que dos ejecuciones no se pisen; escrituras atómicas.

Archivos en ai_lens/paper/ (cámbialo con la variable AI_LENS_PAPER_DIR):
    config.json              capital, riesgo, activos, fecha de inicio
    estado.json              última vela procesada y operaciones ya notificadas
    diario.jsonl             cada setup con la decisión de cada estrategia y la hora en que se registró
    decisiones_claude.json   decisiones de Claude con su hora
    operaciones.csv          todas las operaciones simuladas
    INFORME.md               rendimiento de cada estrategia
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from . import agent, backtest, data, indicator, notify

PAPER_DIR = Path(os.environ.get("AI_LENS_PAPER_DIR", Path(__file__).resolve().parent / "paper"))
STRATEGIES = {
    "indicador": ("240", "todas las señales del indicador"),
    "reglas": ("240", "agente de reglas (AGENT_PROMPT en código)"),
    "reglas_htfD": ("D", "agente de reglas con sesgo diario"),
    "claude": ("240", "Claude con AGENT_PROMPT (decisiones registradas a tiempo)"),
}
WARMUP_DAYS = 400


# ─────────────────────────────────────────────────────────────────────────────
# utilidades de archivos
# ─────────────────────────────────────────────────────────────────────────────
def _p(name: str) -> Path:
    return PAPER_DIR / name


def _read_json(name: str, default):
    f = _p(name)
    return json.loads(f.read_text()) if f.exists() else default


def _write_json(name: str, obj) -> None:
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=PAPER_DIR, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False, default=str)
    os.replace(tmp, _p(name))


def _write_text(name: str, text: str) -> None:
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=PAPER_DIR, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, _p(name))


@contextmanager
def _lock():
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    with open(_p(".lock"), "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Otra ejecución de paper está en marcha; salgo.")
            sys.exit(0)
        yield


def _ts(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _iso(t) -> str:
    return _ts(t).strftime("%Y-%m-%dT%H:%M:%SZ")


def _config() -> dict:
    cfg = _read_json("config.json", None)
    if cfg is None:
        sys.exit("No hay cartera de paper. Arráncala con:  python -m ai_lens.paper init")
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# cálculo
# ─────────────────────────────────────────────────────────────────────────────
def _lenses(cfg: dict) -> dict:
    """{(activo, htf): (lens, i0)} con i0 = primera vela que cierra después del inicio."""
    tf = cfg["tf"]
    sec = data.TF_SECONDS[tf]
    inicio = _ts(cfg["inicio"])
    out = {}
    for a in cfg["activos"]:
        df = data.load(a, tf, start=inicio - pd.Timedelta(days=WARMUP_DAYS))
        for htf in sorted({h for h, _ in STRATEGIES.values()}):
            lens = indicator.compute(df, tf, indicator.Params(htf=htf),
                                     symbol=f"KRAKEN:{data.kraken_symbol(a)}")
            close_t = lens.df["time"] + pd.Timedelta(seconds=sec)
            i0 = int((close_t <= inicio).sum())
            out[(a, htf)] = (lens, i0)
    return out


def _claude_chooser(asset: str, tf: str):
    """Usa la decisión de Claude solo si se registró antes del cierre de la vela siguiente."""
    sec = data.TF_SECONDS[tf]
    dec = {(d["activo"], d["vela"]): d for d in _read_json("decisiones_claude.json", [])}

    def fn(p):
        d = dec.get((asset, p["time"]))
        if not d:
            return {"decision": "SIN_DECISION"}
        if d.get("estado") != "A_TIEMPO" or _ts(d["decidido_en"]) > _ts(p["time"]) + pd.Timedelta(seconds=sec):
            return {"decision": "TARDE"}
        if d.get("invalida"):
            return {"decision": "INVALIDA"}
        # entrada al precio real del momento de la decisión; stop del paquete; objetivos 1,5R/3R recalculados
        return {"decision": d["decision"], "confidence": d.get("confianza"), "concerns": d.get("dudas"),
                "entry": d.get("precio_entrada"), "stop": d.get("stop")}
    return fn


def _choosers(asset: str, tf: str) -> dict:
    rules = agent.Rules()
    return {
        "indicador": lambda p: {"decision": "LONG" if p["event"] == "LONG_SETUP" else "SHORT"},
        "reglas": lambda p: agent.decide(p, rules),
        "reglas_htfD": lambda p: agent.decide(p, rules),
        "claude": _claude_chooser(asset, tf),
    }


def _equity(trades: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, dict]:
    """Capital en USD: el riesgo de cada operación es un % del capital realizado al entrar."""
    cap0, rp = cfg["capital"], cfg["riesgo_pct"] / 100
    if trades.empty:
        return trades, dict(capital=cap0, abierto=0.0, max_dd_pct=0.0)
    ev = []
    for k, t in trades.iterrows():
        ev.append((t["entry_time"], 0, k))
        if t["exit_reason"] != "OPEN":
            ev.append((t["exit_time"], 1, k))
    ev.sort()
    eq, peak, dd = cap0, cap0, 0.0
    risk_amt, pnl, eq_after = {}, {}, {}
    for _, kind, k in ev:
        if kind == 0:
            risk_amt[k] = eq * rp
        else:
            pnl[k] = trades.at[k, "r_net"] * risk_amt[k]
            eq += pnl[k]
            eq_after[k] = eq
            peak = max(peak, eq)
            dd = max(dd, (peak - eq) / peak * 100)
    trades = trades.assign(riesgo_usd=[round(risk_amt[k], 2) for k in trades.index],
                           pnl_usd=[round(pnl.get(k, trades.at[k, "r_net"] * risk_amt[k]), 2) for k in trades.index],
                           capital_tras=[round(eq_after[k], 2) if k in eq_after else None for k in trades.index])
    abierto = float(trades.loc[trades["exit_reason"] == "OPEN", "pnl_usd"].sum())
    return trades, dict(capital=round(eq, 2), abierto=round(abierto, 2), max_dd_pct=round(dd, 2))


def evaluate(cfg: dict, lenses: dict) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    costs = backtest.Costs(cfg["comision_bps"], cfg["desliz_bps"])
    all_tr, all_dec, summary = [], [], {}
    for name, (htf, _) in STRATEGIES.items():
        tr_s, dec_s = [], []
        for a in cfg["activos"]:
            lens, i0 = lenses[(a, htf)]
            tr, dec = backtest.simulate(lens, i0, _choosers(a, cfg["tf"])[name], "full", costs, asset=a)
            tr_s += tr
            dec_s += dec
        tr_df = pd.DataFrame(tr_s)
        if not tr_df.empty:
            tr_df = tr_df.sort_values("entry_time").reset_index(drop=True)
        tr_df, eq = _equity(tr_df, cfg)
        st = backtest.stats(tr_df)
        dec_df = pd.DataFrame(dec_s)
        st["decisiones"] = dec_df["decision"].value_counts().to_dict() if not dec_df.empty else {}
        summary[name] = {**st, **eq}
        if not tr_df.empty:
            all_tr.append(tr_df.assign(estrategia=name))
        if not dec_df.empty:
            all_dec.append(dec_df.assign(estrategia=name))
    tr = pd.concat(all_tr, ignore_index=True) if all_tr else pd.DataFrame()
    dec = pd.concat(all_dec, ignore_index=True) if all_dec else pd.DataFrame()
    return tr, summary, dec


def _report(cfg: dict, tr: pd.DataFrame, summary: dict, last_bar: dict) -> str:
    now = pd.Timestamp.now(tz="UTC")
    dias = (now - _ts(cfg["inicio"])).total_seconds() / 86400
    L = [f"# Paper trading AI Lens",
         "",
         f"Inicio: **{cfg['inicio']}** ({dias:.1f} días) · actualizado {_iso(now)} · "
         f"{', '.join(cfg['activos'])} {cfg['tf']} · capital inicial {cfg['capital']} USD · "
         f"riesgo {cfg['riesgo_pct']} % por operación · costes {cfg['comision_bps']}+{cfg['desliz_bps']} bps por lado",
         "",
         "Última vela procesada: " + ", ".join(f"{a} {t}" for a, t in last_bar.items()),
         "",
         "| Estrategia | Capital USD | Rentab. | Abierto USD | Cerradas | Aciertos | R neto | R/op | Máx. DD | PF | Decisiones en setups |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, s in summary.items():
        ret = (s["capital"] / cfg["capital"] - 1) * 100
        dec = ", ".join(f"{k} {v}" for k, v in s["decisiones"].items()) or "-"
        L.append(f"| {name} | {s['capital']:.2f} | {ret:+.2f} % | {s['abierto']:+.2f} | {s['cerradas']} | {s['aciertos']} | "
                 f"{s['R_total']:+.2f} | {s['R_media']:+.3f} | {s['max_dd_pct']:.1f} % | {s['PF']} | {dec} |")
    L += ["", "Referencia del backtest 2023-2026 (indicador, todas las señales): +0,09R por operación, "
          "29 % de aciertos, rachas de hasta 35 pérdidas seguidas. Con menos de ~100 operaciones cerradas "
          "el resultado del paper es sobre todo ruido.", ""]
    for name, (_, desc) in STRATEGIES.items():
        L.append(f"- **{name}**: {desc}")
    if not tr.empty:
        op = tr[tr["exit_reason"] == "OPEN"]
        if not op.empty:
            L += ["", "## Posiciones abiertas", "", "| Estrategia | Activo | Lado | Entrada (cierre de vela) | Entrada | Stop | T2 | R ahora | USD ahora |",
                  "|---|---|---|---|---|---|---|---|---|"]
            for _, t in op.iterrows():
                L.append(f"| {t['estrategia']} | {t['asset']} | {t['side']} | {_iso(t['entry_time'])} | {t['entry']:.6g} | "
                         f"{t['stop']:.6g} | {t['t2']:.6g} | {t['r_net']:+.2f} | {t['pnl_usd']:+.2f} |")
        cl = tr[tr["exit_reason"] != "OPEN"].sort_values("exit_time", ascending=False).head(30)
        if not cl.empty:
            L += ["", "## Últimas operaciones cerradas", "", "| Estrategia | Activo | Lado | Entrada | Salida | Motivo | R neto | USD |",
                  "|---|---|---|---|---|---|---|---|"]
            for _, t in cl.iterrows():
                L.append(f"| {t['estrategia']} | {t['asset']} | {t['side']} | {_iso(t['entry_time'])} | {_iso(t['exit_time'])} | "
                         f"{t['exit_reason']} | {t['r_net']:+.2f} | {t['pnl_usd']:+.2f} |")
    return "\n".join(L) + "\n"


def _save_outputs(cfg, tr, summary, last_bar):
    if not tr.empty:
        cols = ["estrategia", "asset", "side", "entry_time", "entry", "stop", "t1", "t2", "exit_time", "exit_reason",
                "r_gross", "cost_r", "r_net", "riesgo_usd", "pnl_usd", "capital_tras", "confidence"]
        PAPER_DIR.mkdir(parents=True, exist_ok=True)
        _write_text("operaciones.csv", tr[[c for c in cols if c in tr]].to_csv(index=False))
    rep = _report(cfg, tr, summary, last_bar)
    _write_text("INFORME.md", rep)
    return rep


# ─────────────────────────────────────────────────────────────────────────────
# comandos
# ─────────────────────────────────────────────────────────────────────────────
def cmd_init(a):
    if _p("config.json").exists() and not a.reiniciar:
        sys.exit(f"Ya existe una cartera en {PAPER_DIR}. Usa --reiniciar para empezar de cero.")
    sec = data.TF_SECONDS[a.tf]
    now = pd.Timestamp.now(tz="UTC")
    last_close = pd.Timestamp((now.timestamp() // sec) * sec, unit="s", tz="UTC")
    inicio = _ts(a.inicio) if a.inicio else last_close
    cfg = dict(inicio=_iso(inicio), creado=_iso(now), capital=a.capital, riesgo_pct=a.riesgo, activos=a.activos,
               tf=a.tf, comision_bps=a.comision_bps, desliz_bps=a.desliz_bps,
               nota="hacia delante" if inicio >= last_close else "INCLUYE PERIODO PASADO (no es paper puro)")
    for f in ["estado.json", "diario.jsonl", "decisiones_claude.json", "operaciones.csv", "INFORME.md"]:
        if _p(f).exists():
            _p(f).unlink()
    _write_json("config.json", cfg)
    _write_json("estado.json", {"ultima_vela": {x: cfg["inicio"] for x in a.activos}, "notificadas": []})
    print(f"Cartera de paper creada en {PAPER_DIR}\n{json.dumps(cfg, indent=1, ensure_ascii=False)}")


def cmd_run(a):
    with _lock():
        cfg = _config()
        state = _read_json("estado.json", {"ultima_vela": {}, "notificadas": []})
        sec = data.TF_SECONDS[cfg["tf"]]
        lenses = _lenses(cfg)
        now_iso = _iso(pd.Timestamp.now(tz="UTC"))

        # 1) setups nuevos desde la última ejecución -> diario + aviso
        nuevos = []
        for x in cfg["activos"]:
            ultima = _ts(state["ultima_vela"].get(x, cfg["inicio"]))
            for name, (htf, _) in STRATEGIES.items():
                if name == "claude":
                    continue
                lens, i0 = lenses[(x, htf)]
                d = lens.df
                for i in range(i0, len(d)):
                    close_t = d["time"].iat[i] + pd.Timedelta(seconds=sec)
                    if close_t <= ultima or d["event"].iat[i] not in ("LONG_SETUP", "SHORT_SETUP"):
                        continue
                    p = indicator.packet(lens, i)
                    dec = _choosers(x, cfg["tf"])[name](p)
                    nuevos.append(dict(registrado_en=now_iso, estrategia=name, activo=x, vela=p["time"],
                                       evento=p["event"], decision=dec.get("decision"), confianza=dec.get("confidence"),
                                       dudas=dec.get("concerns"), state_code=p["state_code"], trade=p["trade"]))
            lens240 = lenses[(x, "240")][0]
            state["ultima_vela"][x] = _iso(lens240.df["time"].iat[-1] + pd.Timedelta(seconds=sec))
        if nuevos:
            PAPER_DIR.mkdir(parents=True, exist_ok=True)
            with open(_p("diario.jsonl"), "a") as f:
                for n in nuevos:
                    f.write(json.dumps(n, ensure_ascii=False, default=str) + "\n")
            for (x, vela), grp in pd.DataFrame(nuevos).groupby(["activo", "vela"]):
                t = grp.iloc[0]["trade"]
                lines = [f"{r.estrategia}: {r.evento} -> {r.decision}" for r in grp.itertuples()]
                notify.send(f"AI Lens paper · {x} vela {vela}\n" + "\n".join(lines) +
                            (f"\nentrada {t['entry']} stop {t['stop']} T2 {t['target_2']}" if t else "") +
                            "\nClaude: pendiente (python -m ai_lens.paper pendientes)")

        # 2) rendimiento recalculado desde el inicio
        tr, summary, _ = evaluate(cfg, lenses)
        if not tr.empty:
            cerradas = tr[tr["exit_reason"] != "OPEN"]
            vistas = set(state.get("notificadas", []))
            for _, t in cerradas.iterrows():
                key = f"{t['estrategia']}|{t['asset']}|{_iso(t['entry_time'])}"
                if key not in vistas:
                    vistas.add(key)
                    notify.send(f"AI Lens paper · {t['estrategia']} cierra {t['side']} {t['asset']}: "
                                f"{t['exit_reason']} {t['r_net']:+.2f}R ({t['pnl_usd']:+.2f} USD)")
            state["notificadas"] = sorted(vistas)
        _write_json("estado.json", state)
        rep = _save_outputs(cfg, tr, summary, state["ultima_vela"])
        print(rep if not a.quiet else f"OK {now_iso}: {len(nuevos)} decisiones nuevas")


def cmd_informe(a):
    cfg = _config()
    state = _read_json("estado.json", {"ultima_vela": {}})
    tr, summary, _ = evaluate(cfg, _lenses(cfg))
    print(_save_outputs(cfg, tr, summary, state["ultima_vela"]))


def bundle() -> dict:
    """Estado completo de la cartera en un dict (para guardarlo fuera del disco, p. ej. en el panel)."""
    diario = _p("diario.jsonl").read_text().splitlines() if _p("diario.jsonl").exists() else []
    return {"config": _read_json("config.json", None), "estado": _read_json("estado.json", None),
            "decisiones_claude": _read_json("decisiones_claude.json", []),
            "diario": [json.loads(x) for x in diario if x.strip()]}


def cmd_importar_estado(a):
    """Restaura estado, diario y decisiones de Claude desde un dashboard.json o un bundle exportado."""
    with _lock():
        src = json.loads(Path(a.archivo).read_text())
        b = src.get("_estado_paper", src)
        cfg = _read_json("config.json", None)
        if not b or not b.get("config"):
            print("El archivo no trae estado de la cartera; sigo con el del repositorio.")
            return
        if cfg and b["config"].get("inicio") != cfg.get("inicio"):
            sys.exit(f"El estado es de otra cartera (inicio {b['config'].get('inicio')} y aquí {cfg.get('inicio')}).")
        if cfg is None:
            _write_json("config.json", b["config"])
        if b.get("estado"):
            _write_json("estado.json", b["estado"])
        _write_json("decisiones_claude.json", b.get("decisiones_claude") or [])
        _write_text("diario.jsonl", "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in b.get("diario") or []))
        print(f"Estado restaurado: {len(b.get('decisiones_claude') or [])} decisiones de Claude, "
              f"{len(b.get('diario') or [])} entradas de diario, última vela {b.get('estado', {}).get('ultima_vela')}")


def cmd_pendientes(a):
    cfg = _config()
    sec = data.TF_SECONDS[cfg["tf"]]
    now = pd.Timestamp.now(tz="UTC")
    hechas = {(d["activo"], d["vela"]) for d in _read_json("decisiones_claude.json", [])}
    lenses = _lenses(cfg)
    n = 0
    for x in cfg["activos"]:
        lens, i0 = lenses[(x, "240")]
        for i in range(i0, len(lens.df)):
            if lens.df["event"].iat[i] not in ("LONG_SETUP", "SHORT_SETUP"):
                continue
            p = indicator.packet(lens, i)
            if (x, p["time"]) in hechas:
                continue
            limite = _ts(p["time"]) + pd.Timedelta(seconds=sec)
            if limite < now:
                continue  # ya no se puede decidir a tiempo
            p["_activo"], p["_decidir_antes_de"] = x, _iso(limite)
            print(json.dumps(p, ensure_ascii=False))
            n += 1
    print(f"# {n} setup(s) pendientes de decisión de Claude", file=sys.stderr)


def cmd_decidir(a):
    with _lock():
        cfg = _config()
        sec = data.TF_SECONDS[cfg["tf"]]
        now = pd.Timestamp.now(tz="UTC")
        vela = _iso(a.vela)
        lens, i0 = _lenses(cfg)[(a.activo, "240")]
        close_t = (lens.df["time"] + pd.Timedelta(seconds=sec)).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        idx = [i for i in range(i0, len(lens.df)) if close_t.iat[i] == vela
               and lens.df["event"].iat[i] in ("LONG_SETUP", "SHORT_SETUP")]
        if not idx:
            sys.exit(f"No hay ningún setup de {a.activo} en la vela que cierra {vela}.")
        decs = _read_json("decisiones_claude.json", [])
        if any(d["activo"] == a.activo and d["vela"] == vela for d in decs):
            sys.exit("Esa vela ya tiene decisión de Claude; no se puede cambiar.")
        limite = _ts(vela) + pd.Timedelta(seconds=sec)
        estado = "A_TIEMPO" if now <= limite else "TARDE"
        rec = dict(activo=a.activo, vela=vela, decision=a.decision, confianza=a.confianza, dudas=a.dudas,
                   decidido_en=_iso(now), limite=_iso(limite), estado=estado)
        if a.decision in ("LONG", "SHORT"):
            stop = float(lens.df["t_stop"].iat[idx[0]])
            tk = data.ticker(a.activo)
            precio = tk["ask"] if a.decision == "LONG" else tk["bid"]  # se compra al ask, se vende al bid
            rec.update(precio_entrada=precio, stop=stop, ticker=tk)
            if (a.decision == "LONG" and precio <= stop) or (a.decision == "SHORT" and precio >= stop):
                rec["invalida"] = "el precio actual ya ha pasado el stop"
        decs.append(rec)
        _write_json("decisiones_claude.json", decs)
        extra = f" entrada {rec.get('precio_entrada')} stop {rec.get('stop'):.6g}" if "stop" in rec else ""
        print(f"Decisión registrada: {a.activo} {vela} -> {a.decision} ({estado}){extra}"
              + (f" INVALIDA: {rec['invalida']}" if rec.get("invalida") else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    i = sub.add_parser("init", help="crear la cartera de paper")
    i.add_argument("--capital", type=float, default=1000.0)
    i.add_argument("--riesgo", type=float, default=0.5, help="%% del capital arriesgado por operación")
    i.add_argument("--activos", nargs="+", default=["BTC", "ETH", "SOL"])
    i.add_argument("--tf", default="4h", choices=list(data.TF_SECONDS))
    i.add_argument("--comision-bps", dest="comision_bps", type=float, default=5.0)
    i.add_argument("--desliz-bps", dest="desliz_bps", type=float, default=2.0)
    i.add_argument("--inicio", help="por defecto ahora (paper puro). Una fecha pasada = simulación, no paper")
    i.add_argument("--reiniciar", action="store_true")
    r = sub.add_parser("run", help="actualizar (por defecto)")
    r.add_argument("--quiet", action="store_true")
    sub.add_parser("informe", help="imprimir el informe")
    sub.add_parser("pendientes", help="setups esperando decisión de Claude (JSON por línea)")
    ie = sub.add_parser("importar-estado", help="restaurar el estado guardado en un dashboard.json")
    ie.add_argument("archivo")
    d = sub.add_parser("decidir", help="registrar la decisión de Claude para un setup")
    d.add_argument("--activo", required=True)
    d.add_argument("--vela", required=True, help="hora de cierre de la vela del setup (campo time del paquete)")
    d.add_argument("--decision", required=True, choices=["LONG", "SHORT", "WAIT", "SKIP"])
    d.add_argument("--confianza", type=int)
    d.add_argument("--dudas", nargs="*", default=[])
    a = ap.parse_args()
    if a.cmd is None:
        a.cmd, a.quiet = "run", False
    {"init": cmd_init, "run": cmd_run, "informe": cmd_informe, "pendientes": cmd_pendientes,
     "decidir": cmd_decidir, "importar-estado": cmd_importar_estado}[a.cmd](a)


if __name__ == "__main__":
    main()
