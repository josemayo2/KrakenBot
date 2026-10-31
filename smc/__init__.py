"""Smart Money Concepts (SMC): estrategia propia, reglas explícitas y sin lookahead.

Módulos:
    data        -> carga de CSV y descarga de velas de Kraken (spot y futuros)
    indicators  -> EMA, RSI, ATR, pivotes confirmados, estructura (BOS/CHoCH),
                   barridos de liquidez y Fair Value Gaps
    strategy    -> genera "setups" (órdenes limitadas con SL/TP) barra a barra
    backtest    -> simulador con comisiones de Kraken, slippage y supuestos conservadores
"""
from .strategy import SMCParams, generate_setups
from .backtest import BTConfig, run_backtest, summarize

__all__ = ["SMCParams", "generate_setups", "BTConfig", "run_backtest", "summarize"]
