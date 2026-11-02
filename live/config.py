"""Configuración del bot. Todo se puede sobrescribir con variables de entorno o .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from smc.squeeze import SqueezeParams

DEFAULT_SYMBOLS = ["PF_XBTUSD", "PF_ETHUSD", "PF_SOLUSD", "PF_XRPUSD",
                   "PF_ADAUSD", "PF_LINKUSD", "PF_DOGEUSD"]


def load_dotenv(path: str | Path = ".env") -> None:
    """Lector mínimo de .env (KEY=valor). No pisa variables ya definidas."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _env(name: str, default, conv=str):
    v = os.environ.get(name)
    return default if v in (None, "") else conv(v)


@dataclass
class LiveConfig:
    mode: str = "paper"                     # paper | demo | live
    symbols: list[str] = field(default_factory=lambda: list(DEFAULT_SYMBOLS))
    timeframe: str = "4h"
    history_bars: int = 1500                # velas para calentar indicadores (EMA200, etc.)
    # gestión del riesgo
    risk_pct: float = 0.01                  # pérdida si salta el stop = 1% del capital
    max_notional_per_trade: float = 500.0   # tope de nocional por operación (USD)
    max_total_notional: float = 2500.0      # tope de nocional sumando todas las posiciones
    max_drawdown_halt: float = 0.25         # si el capital cae 25% desde el máximo -> no abre más
    capital_cap: float = 0.0                # >0: el tamaño se calcula como mucho sobre este capital
    paper_initial_equity: float = 1000.0
    # estrategia: la variante validada en walk-forward
    params: SqueezeParams = field(default_factory=lambda: SqueezeParams(
        exit_mode="zero", trend_filter="ema", min_squeeze_bars=6, replicate_bug=True))
    # rutas
    state_path: str = "live_state/state.json"
    trades_log: str = "live_state/trades.csv"
    # credenciales (solo desde entorno)
    api_key: str = ""
    api_secret: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""

    @classmethod
    def from_env(cls, mode: str | None = None) -> "LiveConfig":
        load_dotenv(os.environ.get("BOT_ENV_FILE", ".env"))
        c = cls()
        c.mode = mode or _env("BOT_MODE", c.mode)
        c.symbols = _env("BOT_SYMBOLS", c.symbols, lambda s: [x.strip() for x in s.split(",") if x.strip()])
        c.risk_pct = _env("BOT_RISK_PCT", c.risk_pct, float)
        c.max_notional_per_trade = _env("BOT_MAX_NOTIONAL_PER_TRADE", c.max_notional_per_trade, float)
        c.max_total_notional = _env("BOT_MAX_TOTAL_NOTIONAL", c.max_total_notional, float)
        c.max_drawdown_halt = _env("BOT_MAX_DRAWDOWN_HALT", c.max_drawdown_halt, float)
        c.capital_cap = _env("BOT_CAPITAL_CAP", c.capital_cap, float)
        c.paper_initial_equity = _env("BOT_PAPER_EQUITY", c.paper_initial_equity, float)
        c.state_path = _env("BOT_STATE_PATH", f"live_state/state_{c.mode}.json")
        c.trades_log = _env("BOT_TRADES_LOG", f"live_state/trades_{c.mode}.csv")
        prefix = "KRAKEN_DEMO" if c.mode == "demo" else "KRAKEN_FUTURES"
        c.api_key = _env(f"{prefix}_API_KEY", "")
        c.api_secret = _env(f"{prefix}_API_SECRET", "")
        c.telegram_token = _env("TELEGRAM_TOKEN", "")
        c.telegram_chat_id = _env("TELEGRAM_CHAT_ID", "")
        return c
