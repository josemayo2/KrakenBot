#!/usr/bin/env python3
"""Bot Squeeze Momentum para Kraken Futures. Pensado para ejecutarse con cron cada 4h.

Cada ejecución (justo después del cierre de una vela de 4h):
  1. Descarga las últimas velas CERRADAS de cada mercado y calcula las señales con
     exactamente el mismo código que el backtest (smc.squeeze).
  2. Concilia el estado local con el exchange (¿saltó un stop? ¿falta un stop?).
  3. Señal de salida -> cancela el stop y cierra a mercado (reduce-only).
  4. Señal de entrada -> abre a mercado y coloca un STOP en el exchange (reduce-only),
     así el stop funciona aunque el bot o el servidor se caigan.

Modos:
  paper : simulado en local, sin claves (por defecto)
  demo  : cuenta demo de Kraken Futures (demo-futures.kraken.com), dinero ficticio
  live  : cuenta real. Exige --confirm-live

Uso:
  python3 -m live.squeeze_bot --mode paper
  python3 -m live.squeeze_bot --mode demo
  python3 -m live.squeeze_bot --mode live --confirm-live
  python3 -m live.squeeze_bot --mode live --status        # solo muestra estado
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from smc.data import download_kraken_futures
from smc.squeeze import generate_squeeze_signals

from .brokers import BrokerError, KrakenFuturesBroker, PaperBroker
from .config import LiveConfig

TF_DELTA = {"1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1)}
SYSTEM = "squeeze_bot"


# ------------------------------------------------------------------ utilidades
class Notifier:
    def __init__(self, token: str = "", chat_id: str = "", prefix: str = ""):
        self.token, self.chat_id, self.prefix = token, chat_id, prefix

    def send(self, msg: str) -> None:
        text = f"{self.prefix}{msg}"
        print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z] {text}", flush=True)
        if self.token and self.chat_id:
            try:
                import requests
                r = requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                                  data={"chat_id": self.chat_id, "text": text}, timeout=10)
                if not r.ok:
                    print(f"(Telegram rechazó el mensaje: {r.status_code} {r.text[:200]})", flush=True)
            except Exception as e:  # el aviso nunca debe tumbar el bot
                print(f"(Telegram falló: {e})", flush=True)


def load_state(path: str) -> dict:
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text())
    return {"positions": {}, "last_bar": {}, "peak_equity": None, "halted": False}


def save_state(path: str, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.replace(p)  # escritura atómica


def log_trade(path: str, row: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    with p.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["time", "symbol", "action", "side", "size", "price",
                                          "stop", "equity", "note"])
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in w.fieldnames})


def fetch_closed_candles(symbol: str, tf: str, bars: int, now: pd.Timestamp) -> pd.DataFrame:
    start = (now - TF_DELTA[tf] * (bars + 5)).strftime("%Y-%m-%d %H:%M")
    df = download_kraken_futures(symbol, tf, start=start, pause=0.2)
    return df[df.index + TF_DELTA[tf] <= now]  # descarta la vela en formación


# ------------------------------------------------------------------ núcleo
def run_once(cfg: LiveConfig, broker, fetch, notifier: Notifier, now: pd.Timestamp | None = None) -> dict:
    """Una pasada completa. `fetch(symbol) -> DataFrame` de velas cerradas."""
    now = now or pd.Timestamp.now(tz="UTC")
    state = load_state(cfg.state_path)
    positions: dict = state["positions"]
    candles = {}

    # paper: comprobar stops con las velas nuevas y marcar precios
    if isinstance(broker, PaperBroker):
        for sym in list(positions):
            df = candles[sym] = fetch(sym)
            opened = pd.Timestamp(positions[sym]["opened"])
            px = broker.simulate_stops(sym, df[df.index >= opened.floor(TF_DELTA[cfg.timeframe])])
            if px is not None:
                positions[sym]["stop_fill"] = px
        equity = broker.equity({s: candles[s]["close"].iloc[-1] for s in candles})
    else:
        equity = broker.equity()

    peak = max(state.get("peak_equity") or equity, equity)
    state["peak_equity"] = peak
    dd = 1 - equity / peak if peak > 0 else 0.0
    if dd >= cfg.max_drawdown_halt and not state.get("halted"):
        state["halted"] = True
        notifier.send(f"🛑 Drawdown {dd:.1%} >= {cfg.max_drawdown_halt:.0%}. No se abren más posiciones "
                      f"(las abiertas se siguen gestionando). Para reanudar: poner halted=false en {cfg.state_path}")
    halted = state.get("halted", False)
    processed = 0

    for sym in cfg.symbols:
        try:
            df = candles.get(sym)
            if df is None:
                df = candles[sym] = fetch(sym)
            if len(df) < 300:
                notifier.send(f"⚠️ {sym}: solo {len(df)} velas, me lo salto")
                continue
            last_t = df.index[-1]
            if state["last_bar"].get(sym) == str(last_t):
                continue  # esta vela ya se procesó (cron duplicado)
            entries, exits = generate_squeeze_signals(df, cfg.params)
            L = len(df) - 1
            close = float(df["close"].iloc[-1])
            st = positions.get(sym)
            ex = broker.position(sym)

            # --- conciliación con el exchange
            if st and not ex:
                exit_px = st.pop("stop_fill", st["stop"])
                notifier.send(f"🔴 {sym}: posición {'LARGA' if st['side'] == 1 else 'CORTA'} cerrada por STOP "
                              f"(~{exit_px:.6g}). Entrada {st['entry']:.6g}")
                log_trade(cfg.trades_log, {"time": now, "symbol": sym, "action": "stop", "side": st["side"],
                                           "size": st["size"], "price": exit_px, "stop": st["stop"],
                                           "equity": equity})
                for oid in broker.open_order_ids(sym):
                    broker.cancel(oid)
                positions.pop(sym)
                st = None
            elif ex and not st:
                notifier.send(f"⚠️ {sym}: hay una posición en el exchange que el bot no abrió. No la toco.")
                state["last_bar"][sym] = str(last_t)
                continue
            elif st and ex and st.get("stop_id") not in broker.open_order_ids(sym):
                st["stop_id"] = broker.place_stop(sym, -st["side"], ex["size"], st["stop"],
                                                  f"{SYSTEM}-{sym}-restop-{int(time.time())}")
                notifier.send(f"⚠️ {sym}: faltaba el stop en el exchange; recolocado en {st['stop']:.6g}")

            # --- salida por señal
            if st:
                want_exit = exits["exit_long"].iloc[L] if st["side"] == 1 else exits["exit_short"].iloc[L]
                if want_exit:
                    if st.get("stop_id"):
                        broker.cancel(st["stop_id"])
                    px = broker.market(sym, -st["side"], st["size"], reduce_only=True,
                                       tag=f"{SYSTEM}-{sym}-exit-{last_t:%Y%m%d%H}", ref_price=close)
                    r = st["side"] * (px - st["entry"]) / st["stop_dist"]
                    notifier.send(f"⚪ {sym}: cierre por señal a {px:.6g} (entrada {st['entry']:.6g}, {r:+.2f}R)")
                    log_trade(cfg.trades_log, {"time": now, "symbol": sym, "action": "exit", "side": st["side"],
                                               "size": st["size"], "price": px, "stop": st["stop"],
                                               "equity": equity, "note": f"{r:+.2f}R"})
                    positions.pop(sym)
                    st = None

            # --- entrada
            sig = entries[entries["bar"] == L]
            if not sig.empty and st is None:
                side = int(sig["side"].iloc[0])
                stop_dist = float(sig["stop_dist"].iloc[0])
                side_txt = "LARGO" if side == 1 else "CORTO"
                if halted:
                    notifier.send(f"⏸️ {sym}: señal {side_txt} ignorada (bot en pausa por drawdown)")
                else:
                    sizing_equity = min(equity, cfg.capital_cap) if cfg.capital_cap > 0 else equity
                    size = cfg.risk_pct * sizing_equity / stop_dist
                    size = min(size, cfg.max_notional_per_trade / close)
                    used = sum(p["size"] * p["entry"] for p in positions.values())
                    size = min(size, max(0.0, cfg.max_total_notional - used) / close)
                    size = broker.round_size(sym, size)
                    if size <= 0 or size < broker.min_size(sym):
                        notifier.send(f"⏭️ {sym}: señal {side_txt} pero el tamaño ({size}) es menor que el mínimo "
                                      f"o se superaría el nocional total")
                    else:
                        tag = f"{SYSTEM}-{sym}-entry-{last_t:%Y%m%d%H}"
                        px = broker.market(sym, side, size, tag=tag, ref_price=close)
                        stop = broker.round_price(sym, px - side * stop_dist)
                        try:
                            stop_id = broker.place_stop(sym, -side, size, stop, f"{SYSTEM}-{sym}-stop-{last_t:%Y%m%d%H}")
                        except Exception as e:
                            broker.market(sym, -side, size, reduce_only=True, tag=f"{tag}-abort", ref_price=close)
                            notifier.send(f"🚨 {sym}: no se pudo colocar el stop ({e}). Posición cerrada por seguridad.")
                            raise
                        positions[sym] = {"side": side, "size": size, "entry": px, "stop": stop,
                                          "stop_dist": stop_dist, "stop_id": stop_id,
                                          "opened": str(last_t + TF_DELTA[cfg.timeframe])}
                        risk_usd = size * stop_dist
                        notifier.send(f"{'🟢' if side == 1 else '🟣'} {sym}: {side_txt} {size} @ {px:.6g} | "
                                      f"stop {stop:.6g} | nocional ${size * px:,.0f} | riesgo ${risk_usd:,.2f}")
                        log_trade(cfg.trades_log, {"time": now, "symbol": sym, "action": "entry", "side": side,
                                                   "size": size, "price": px, "stop": stop, "equity": equity})
            state["last_bar"][sym] = str(last_t)
            processed += 1
        except Exception as e:
            notifier.send(f"❌ {sym}: error {type(e).__name__}: {e}")
            traceback.print_exc()
        finally:
            save_state(cfg.state_path, state)

    save_state(cfg.state_path, state)
    open_txt = ", ".join(f"{s.replace('PF_', '').replace('USD', '')} {'L' if p['side'] == 1 else 'S'}"
                         for s, p in positions.items()) or "ninguna"
    print(f"[{now:%Y-%m-%d %H:%M}Z] ejecución OK | velas nuevas: {processed} | capital {equity:,.2f} | "
          f"posiciones: {open_txt}{' | EN PAUSA' if state.get('halted') else ''}", flush=True)
    if processed and now.hour == 0 and now.minute < 30:  # resumen diario tras la vela de las 00:00 UTC
        notifier.send(f"💓 Bot activo | capital {equity:,.2f} $ | posiciones: {open_txt}"
                      f"{' | ⏸️ EN PAUSA' if state.get('halted') else ''}")
    return state


def test_orders(broker, sym: str, notifier: Notifier) -> None:
    """Recorre todo el ciclo de órdenes con el tamaño mínimo para verificar la integración."""
    if broker.position(sym):
        sys.exit(f"Ya hay una posición en {sym}; elige otro símbolo para la prueba.")
    size = broker.min_size(sym)
    tag = f"{SYSTEM}-test-{int(time.time())}"
    print(f"1) Capital: {broker.equity():,.2f}")
    px = broker.market(sym, 1, size, tag=f"{tag}-buy")
    print(f"2) Compra a mercado {size} {sym} @ {px}")
    pos = broker.position(sym)
    print(f"3) Posición vista en el exchange: {pos}")
    stop_px = broker.round_price(sym, px * 0.9)
    oid = broker.place_stop(sym, -1, size, stop_px, f"{tag}-stop")
    print(f"4) Stop reduce-only colocado en {stop_px} (id {oid})")
    ok = oid in broker.open_order_ids(sym)
    print(f"5) Stop visible en órdenes abiertas: {ok}")
    broker.cancel(oid)
    print("6) Stop cancelado")
    out = broker.market(sym, -1, size, reduce_only=True, tag=f"{tag}-close")
    print(f"7) Cierre reduce-only @ {out}")
    left = broker.position(sym)
    print(f"8) Posición final: {left}")
    if pos and ok and not left:
        notifier.send(f"✅ Prueba de órdenes OK en {sym}")
    else:
        notifier.send(f"🚨 Prueba de órdenes con problemas en {sym}: revisa la salida")


def build_broker(cfg: LiveConfig):
    if cfg.mode == "paper":
        return PaperBroker(str(Path(cfg.state_path).with_name("paper_broker.json")), cfg.paper_initial_equity)
    return KrakenFuturesBroker(cfg.api_key, cfg.api_secret, demo=(cfg.mode == "demo"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["paper", "demo", "live"], default=None)
    ap.add_argument("--confirm-live", action="store_true", help="obligatorio para operar con dinero real")
    ap.add_argument("--status", action="store_true", help="muestra saldo, posiciones y estado, sin operar")
    ap.add_argument("--test-orders", metavar="SYMBOL",
                    help="prueba de conexión: compra el tamaño mínimo, pone un stop, lo cancela y cierra")
    a = ap.parse_args()

    cfg = LiveConfig.from_env(a.mode)
    if cfg.mode == "live" and not (a.confirm_live or a.status or a.test_orders):
        sys.exit("Modo live: añade --confirm-live para operar con dinero real.")
    tag = {"paper": "[PAPER] ", "demo": "[DEMO] ", "live": ""}[cfg.mode]
    notifier = Notifier(cfg.telegram_token, cfg.telegram_chat_id, prefix=tag)
    try:
        broker = build_broker(cfg)
    except Exception as e:
        notifier.send(f"❌ No se pudo conectar con el broker: {e}")
        raise

    if a.status:
        print(f"Modo: {cfg.mode} | capital: {broker.equity():,.2f}")
        if cfg.mode != "paper":
            for s in cfg.symbols:
                broker._market(s)  # falla con un mensaje claro si el mercado no existe en Kraken
        print(f"Mercados: {', '.join(cfg.symbols)}")
        print(f"Telegram: {'activado' if cfg.telegram_token and cfg.telegram_chat_id else 'desactivado'}")
        for s in cfg.symbols:
            p = broker.position(s)
            if p:
                print(f"  {s}: {'LARGO' if p['side'] == 1 else 'CORTO'} {p['size']} @ {p['entry']}")
        print(json.dumps(load_state(cfg.state_path), indent=2))
        return

    if a.test_orders:
        if cfg.mode == "paper":
            sys.exit("--test-orders es para demo o live")
        test_orders(broker, a.test_orders.upper(), notifier)
        return

    now = pd.Timestamp.now(tz="UTC")
    run_once(cfg, broker, lambda s: fetch_closed_candles(s, cfg.timeframe, cfg.history_bars, now), notifier, now)


if __name__ == "__main__":
    main()
