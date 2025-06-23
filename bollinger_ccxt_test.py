#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import ccxt
import time
import json

API_KEY    = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'
SYSTEM_NAME = 'bollinger_kraken'
SYMBOL_UI  = 'BTC/USD:USD'

def main():
    exchange = ccxt.krakenfutures({
        'apiKey':          API_KEY,
        'secret':          API_SECRET,
        'enableRateLimit': True,
    })

    clientOrderId = f"{SYSTEM_NAME}-{int(time.time()*1000)}"
    params = {
        'clientOrderId': clientOrderId,
        'timeInForce':   'IOC',    # immediate-or-cancel
        'reduceOnly':    False,
        'postOnly':      False,
        'leverage':      1,
    }

    print(f"▶ Sending MARKET order with clientOrderId={clientOrderId}")
    order = exchange.create_market_order(
        SYMBOL_UI,
        'buy',
        0.001,    # pequeño tamaño para test
        None,
        params
    )
    print("✅ Market order response:")
    print(json.dumps(order, indent=2))

if __name__ == '__main__':
    main()
