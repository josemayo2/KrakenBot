
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
renko2_maker.py
Sistema RENKO2 para Kraken Futures con lógica simétrica BUY/SELL.
Incluye STOP LOSS, TAKE PROFIT y TRAILING STOP.
"""

import ccxt
import time
from datetime import datetime, timezone
import requests
import pytz
import logging

# === CONFIGURACIÓN ===
API_KEY = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'
SYMBOL = 'BTC/USD:USD'
ORDER_SIZE = 0.002
BOX_SIZE = 20.0
STOP_LOSS_PCT = 0.01
TAKE_PROFIT_PCT = 0.011
TRAILING_START_PCT = 0.0049
TRAILING_STEP_PCT = 0.00003

TELEGRAM_TOKEN = '8127892735:AAGZDT0xOzLpV01ISH5LKN3wG7INEL12Xrk'
TELEGRAM_CHAT_ID = '925121458'

# === Inicializar exchange Kraken Futures ===
exchange = ccxt.krakenfutures({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
})

# === Inicializar logging ===
def log(msg, notify=False):
    ts = datetime.now(timezone.utc).isoformat(timespec='seconds')
    print(f"[renko2_maker] {ts} {msg}")
    if notify:
        try:
            requests.post(f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
                          data={'chat_id': TELEGRAM_CHAT_ID, 'text': f"[renko2_maker] {msg}"})
        except Exception as e:
            print(f"[renko2_maker] ⚠️ Error enviando Telegram: {e}")

log("🧱 Bot RENKO2 activado", notify=True)

# === Inicialización de Renko ===
def get_price():
    ticker = exchange.fetch_ticker(SYMBOL)
    return ticker['last']

price = get_price()
renko_max = price + BOX_SIZE / 2
renko_min = price - BOX_SIZE / 2
log(f"🔄 Init Renko → box={BOX_SIZE:.2f}, Max={renko_max:.2f}, Min={renko_min:.2f}")

def place_order_with_stops(side, price_entry):
    try:
        params = {"reduce_only": False}
        order = exchange.create_order(
            symbol=SYMBOL,
            type='limit',
            side=side,
            amount=ORDER_SIZE,
            price=price_entry,
            params=params
        )
        log(f"✅ Orden LIMIT {side.upper()} colocada @ {price_entry:.2f}", notify=True)

        stop_loss_price = price_entry * (1 - STOP_LOSS_PCT) if side == 'buy' else price_entry * (1 + STOP_LOSS_PCT)
        take_profit_price = price_entry * (1 + TAKE_PROFIT_PCT) if side == 'buy' else price_entry * (1 - TAKE_PROFIT_PCT)
        trailing_start = price_entry * TRAILING_START_PCT
        trailing_step = price_entry * TRAILING_STEP_PCT

        log(f"📉 STOP @ {stop_loss_price:.2f}, 🎯 TP @ {take_profit_price:.2f}", notify=True)
        log(f"🔃 Trailing desde +{TRAILING_START_PCT*100:.2f}% paso +{TRAILING_STEP_PCT*100:.4f}%", notify=False)

    except Exception as e:
        log(f"⚠️ Error al colocar orden {side.upper()}: {e}", notify=True)

# === Bucle principal ===
while True:
    try:
        price = get_price()
        log(f"🔄 Precio actual: {price:.2f}")

        if price >= renko_max:
            renko_min = renko_max
            renko_max += BOX_SIZE
            log(f"🔄 Renko updated → box={BOX_SIZE:.2f}, Max={renko_max:.2f}, Min={renko_min:.2f}")
            log(f"🚨 Señal detectada: entrada BUY (LIMIT)")
            place_order_with_stops('buy', round(price + 3, 2))  # +3 por seguridad

        elif price <= renko_min:
            renko_max = renko_min
            renko_min -= BOX_SIZE
            log(f"🔄 Renko updated → box={BOX_SIZE:.2f}, Max={renko_max:.2f}, Min={renko_min:.2f}")
            log(f"🚨 Señal detectada: entrada SELL (LIMIT)")
            place_order_with_stops('sell', round(price - 3, 2))  # -3 por seguridad

        time.sleep(60)

    except Exception as e:
        log(f"⚠️ Error en ciclo principal: {e}")
        time.sleep(15)
