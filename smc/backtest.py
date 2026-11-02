"""Simulador barra a barra con supuestos conservadores.

- Un setup generado al cierre de t solo puede llenarse desde t+1.
- Orden limitada: se llena si el precio la toca; si la vela abre más allá, se llena
  a la apertura (precio real disponible). Comisión maker.
- Si el precio llega al TP sin haber llenado la entrada, la orden se cancela.
- En la vela de entrada solo se comprueba el SL (nunca se acredita un TP ese mismo bar).
- Si en una misma vela se tocan SL y TP, se asume SL (peor caso).
- Gap a través del SL: salida a la apertura. SL = orden stop -> comisión taker + slippage.
- TP = orden limitada -> comisión maker, sin slippage.
- Tamaño: riesgo fijo (% del capital) / distancia al stop, con apalancamiento máximo.
- No se modela funding de perpetuos (añadir antes de ir a real si se mantienen
  posiciones muchas horas).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CostModel:
    maker: float
    taker: float
    slippage: float  # fracción del precio en salidas por stop / mercado


# Tarifas base de Kraken (nivel de volumen más bajo). Revísalas en tu cuenta.
COSTS = {
    "futures": CostModel(maker=0.0002, taker=0.0005, slippage=0.0002),
    "spot": CostModel(maker=0.0025, taker=0.0040, slippage=0.0002),
    "zero": CostModel(maker=0.0, taker=0.0, slippage=0.0),
}


@dataclass
class BTConfig:
    initial_equity: float = 10_000.0
    risk_per_trade: float = 0.01
    max_leverage: float = 3.0
    costs: str = "futures"
    max_hold_bars: int | None = None


def run_backtest(df: pd.DataFrame, setups: pd.DataFrame, cfg: BTConfig | None = None):
    cfg = cfg or BTConfig()
    cm = COSTS[cfg.costs]
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    idx = df.index
    n = len(df)
    by_bar = {int(r.bar): r for r in setups.itertuples(index=False)} if len(setups) else {}

    equity = cfg.initial_equity
    eq_curve = np.empty(n)
    pending = None
    pos = None
    trades = []

    def close_pos(t, price, reason, fee_rate):
        nonlocal equity, pos
        side, qty = pos["side"], pos["qty"]
        exit_fee = qty * price * fee_rate
        gross = side * qty * (price - pos["entry"])
        pnl = gross - pos["entry_fee"] - exit_fee
        equity += gross - exit_fee  # la comisión de entrada ya se descontó
        trades.append({
            "entry_time": idx[pos["bar"]], "exit_time": idx[t], "side": side,
            "entry": pos["entry"], "exit": price, "sl": pos["sl"], "tp": pos["tp"],
            "qty": qty, "fees": pos["entry_fee"] + exit_fee, "pnl": pnl,
            "r": pnl / pos["risk_cash"], "reason": reason, "bars": t - pos["bar"],
            "setup_time": pos["setup_time"],
        })
        pos = None

    for t in range(n):
        # 1) orden pendiente (colocada en un cierre anterior)
        if pos is None and pending is not None:
            s = pending
            side = s.side
            if t > s.expiry_bar:
                pending = None
            elif (side == 1 and l[t] > s.entry and h[t] >= s.tp) or \
                 (side == -1 and h[t] < s.entry and l[t] <= s.tp):
                pending = None  # se fue al objetivo sin nosotros
            else:
                touched = l[t] <= s.entry if side == 1 else h[t] >= s.entry
                if touched:
                    gap = o[t] <= s.entry if side == 1 else o[t] >= s.entry
                    fill = o[t] if gap else s.entry
                    risk_px = (fill - s.sl) * side
                    pending = None
                    if risk_px > 0:
                        qty = cfg.risk_per_trade * equity / risk_px
                        qty = min(qty, cfg.max_leverage * equity / fill)
                        fee = qty * fill * cm.maker
                        equity -= fee
                        pos = {"side": side, "entry": fill, "sl": s.sl, "tp": s.tp,
                               "qty": qty, "bar": t, "entry_fee": fee,
                               "risk_cash": qty * risk_px, "setup_time": s.time}
                        # misma vela: solo stop (conservador)
                        if (side == 1 and l[t] <= s.sl) or (side == -1 and h[t] >= s.sl):
                            close_pos(t, s.sl - side * s.sl * cm.slippage, "sl", cm.taker)
        # 2) gestión de la posición abierta (velas posteriores a la entrada)
        elif pos is not None:
            side, sl, tp = pos["side"], pos["sl"], pos["tp"]
            if (side == 1 and o[t] <= sl) or (side == -1 and o[t] >= sl):
                close_pos(t, o[t] * (1 - side * cm.slippage), "sl_gap", cm.taker)
            elif (side == 1 and l[t] <= sl) or (side == -1 and h[t] >= sl):
                close_pos(t, sl * (1 - side * cm.slippage), "sl", cm.taker)
            elif (side == 1 and o[t] >= tp) or (side == -1 and o[t] <= tp):
                close_pos(t, o[t], "tp_gap", cm.maker)
            elif (side == 1 and h[t] >= tp) or (side == -1 and l[t] <= tp):
                close_pos(t, tp, "tp", cm.maker)
            elif cfg.max_hold_bars and t - pos["bar"] >= cfg.max_hold_bars:
                close_pos(t, c[t] * (1 - side * cm.slippage), "time", cm.taker)

        # 3) nuevo setup al cierre de t (solo si estamos fuera)
        if pos is None and t in by_bar:
            pending = by_bar[t]

        mtm = pos["side"] * pos["qty"] * (c[t] - pos["entry"]) if pos else 0.0
        eq_curve[t] = equity + mtm

    if pos is not None:  # cerrar al final de los datos
        close_pos(n - 1, c[-1], "eod", cm.taker)
        eq_curve[-1] = equity

    tr = pd.DataFrame(trades)
    return tr, pd.Series(eq_curve, index=idx, name="equity")


def run_signal_backtest(df: pd.DataFrame, entries: pd.DataFrame, exits: pd.DataFrame,
                        cfg: BTConfig | None = None):
    """Simulador para estrategias de entrada a mercado y salida por señal.

    - Señal de entrada al cierre de t -> compra/venta a mercado en la apertura de t+1
      (taker + slippage). Stop = precio de entrada ∓ stop_dist; TP opcional a tp_r * riesgo.
    - Señal de salida al cierre de t -> cierre a mercado en la apertura de t+1.
    - Stop y TP se vigilan dentro de cada vela, incluida la de entrada (se entra en la
      apertura, así que no hay ambigüedad de orden). Si se tocan ambos -> stop.
    - Si en la misma apertura hay salida y entrada contraria, primero se cierra y luego se abre.
    """
    cfg = cfg or BTConfig()
    cm = COSTS[cfg.costs]
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    xl, xs = exits["exit_long"].to_numpy(), exits["exit_short"].to_numpy()
    idx = df.index
    n = len(df)
    by_bar = {int(r.bar): r for r in entries.itertuples(index=False)} if len(entries) else {}

    equity = cfg.initial_equity
    eq_curve = np.empty(n)
    pos = None
    pend_entry = None
    pend_exit = False
    trades = []

    def close_pos(t, price, reason, fee_rate):
        nonlocal equity, pos
        side, qty = pos["side"], pos["qty"]
        exit_fee = qty * price * fee_rate
        gross = side * qty * (price - pos["entry"])
        equity += gross - exit_fee
        pnl = gross - pos["entry_fee"] - exit_fee
        trades.append({
            "entry_time": idx[pos["bar"]], "exit_time": idx[t], "side": side,
            "entry": pos["entry"], "exit": price, "sl": pos["sl"], "tp": pos["tp"],
            "qty": qty, "fees": pos["entry_fee"] + exit_fee, "pnl": pnl,
            "r": pnl / pos["risk_cash"], "reason": reason, "bars": t - pos["bar"],
            "mfe_r": pos["mfe"] / pos["risk_px"],
        })
        pos = None

    for t in range(n):
        # 1) apertura: salida pendiente y luego entrada pendiente
        if pos is not None and pend_exit:
            close_pos(t, o[t] * (1 - pos["side"] * cm.slippage), "signal", cm.taker)
        pend_exit = False
        if pos is None and pend_entry is not None:
            s = pend_entry
            fill = o[t] * (1 + s.side * cm.slippage)
            risk_px = s.stop_dist
            if risk_px > 0:
                qty = min(cfg.risk_per_trade * equity / risk_px, cfg.max_leverage * equity / fill)
                fee = qty * fill * cm.taker
                equity -= fee
                pos = {"side": s.side, "entry": fill, "sl": fill - s.side * risk_px,
                       "tp": fill + s.side * s.tp_r * risk_px if s.tp_r > 0 else np.nan,
                       "qty": qty, "bar": t, "entry_fee": fee, "risk_px": risk_px,
                       "risk_cash": qty * risk_px, "mfe": 0.0}
        pend_entry = None

        # 2) dentro de la vela: stop / take profit
        if pos is not None:
            side, sl, tp = pos["side"], pos["sl"], pos["tp"]
            gap_sl = (side == 1 and o[t] <= sl) or (side == -1 and o[t] >= sl)
            hit_sl = (side == 1 and l[t] <= sl) or (side == -1 and h[t] >= sl)
            hit_tp = not np.isnan(tp) and ((side == 1 and h[t] >= tp) or (side == -1 and l[t] <= tp))
            if gap_sl and pos["bar"] != t:
                close_pos(t, o[t] * (1 - side * cm.slippage), "sl_gap", cm.taker)
            elif hit_sl:
                close_pos(t, sl * (1 - side * cm.slippage), "sl", cm.taker)
            elif hit_tp:
                close_pos(t, tp, "tp", cm.maker)
            else:
                fav = (h[t] - pos["entry"]) if side == 1 else (pos["entry"] - l[t])
                pos["mfe"] = max(pos["mfe"], fav)
                if cfg.max_hold_bars and t - pos["bar"] >= cfg.max_hold_bars:
                    pend_exit = True

        # 3) cierre: señales para la apertura siguiente
        if pos is not None and ((pos["side"] == 1 and xl[t]) or (pos["side"] == -1 and xs[t])):
            pend_exit = True
        if t in by_bar:
            s = by_bar[t]
            if pos is None or (pend_exit and pos["side"] != s.side):
                pend_entry = s

        eq_curve[t] = equity + (pos["side"] * pos["qty"] * (c[t] - pos["entry"]) if pos else 0.0)

    if pos is not None:
        close_pos(n - 1, c[-1], "eod", cm.taker)
        eq_curve[-1] = equity

    return pd.DataFrame(trades), pd.Series(eq_curve, index=idx, name="equity")


def summarize(trades: pd.DataFrame, equity: pd.Series, df: pd.DataFrame,
              initial_equity: float = 10_000.0) -> dict:
    out = {"bars": len(df), "start": str(df.index[0]), "end": str(df.index[-1])}
    dt = pd.Series(df.index).diff().median()
    bars_per_year = pd.Timedelta(days=365) / dt if pd.notna(dt) and dt > pd.Timedelta(0) else np.nan
    years = len(df) / bars_per_year if bars_per_year else np.nan

    n = len(trades)
    out["trades"] = n
    if n:
        wins = trades["pnl"] > 0
        gp, gl = trades.loc[wins, "pnl"].sum(), -trades.loc[~wins, "pnl"].sum()
        out.update({
            "win_rate_pct": 100 * wins.mean(),
            "avg_R": trades["r"].mean(),
            "median_R": trades["r"].median(),
            "profit_factor": gp / gl if gl > 0 else np.inf,
            "fees_paid": trades["fees"].sum(),
            "longs": int((trades["side"] == 1).sum()),
            "shorts": int((trades["side"] == -1).sum()),
            "avg_bars_held": trades["bars"].mean(),
            "trades_per_year": n / years if years else np.nan,
        })
    final = equity.iloc[-1]
    out["final_equity"] = final
    out["return_pct"] = 100 * (final / initial_equity - 1)
    out["cagr_pct"] = 100 * ((final / initial_equity) ** (1 / years) - 1) if years and final > 0 else np.nan
    dd = equity / equity.cummax() - 1
    out["max_drawdown_pct"] = 100 * dd.min()
    r = equity.pct_change().dropna()
    out["sharpe"] = (r.mean() / r.std() * np.sqrt(bars_per_year)) if r.std() > 0 else np.nan
    out["buy_hold_pct"] = 100 * (df["close"].iloc[-1] / df["close"].iloc[0] - 1)
    return out
