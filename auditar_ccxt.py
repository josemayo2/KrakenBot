#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auditar_ccxt.py

Auditor para 5 bots en Kraken Futures usando CCXT,
ahora mostrando la hora de última operación en tu zona local.
"""

import ccxt
from datetime import datetime, timezone

# ——— Configuración ———
SISTEMAS = [
    'ichimoku_kraken_telegram_v2',
    'konkorde_live_telegram',
    'mix_kraken',
    'renko_kraken',
    'bollinger_kraken',
]

API_KEY    = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'
SYMBOL_UI  = 'BTC/USD:USD'
# ——————————————————

def main():
    exchange = ccxt.krakenfutures({
        'apiKey':          API_KEY,
        'secret':          API_SECRET,
        'enableRateLimit': True,
    })

    # 1) Obtener órdenes abiertas y cerradas
    open_orders   = exchange.fetch_open_orders(SYMBOL_UI)
    closed_orders = exchange.fetch_closed_orders(SYMBOL_UI)
    orders = open_orders + closed_orders

    print(f"{'Sistema':35} {'Ha operado?':10} Última operación")
    print("-" * 70)
    for sistema in SISTEMAS:
        # Filtrar órdenes enviadas por este sistema
        my_orders = [
            o for o in orders
            if (o.get('clientOrderId') or '').startswith(sistema)
        ]
        # Recopilar timestamps de ejecución (ms)
        executed_ts = [o['timestamp'] for o in my_orders if o.get('timestamp')]
        if not executed_ts:
            print(f"{sistema:35} {'No':10} -")
        else:
            last_ts = max(executed_ts)
            # Convertir de UTC a hora local
            last_dt = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc) \
                             .astimezone() \
                             .isoformat()
            print(f"{sistema:35} {'Sí':10} {last_dt}")

if __name__ == '__main__':
    main()
