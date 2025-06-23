#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bollinger_ccxt_hourly.py

Sistema de Bandas de Bollinger en 1h para Kraken Futures con CCXT.
Incluye alertas por Telegram al detectar señal y colocar orden.
"""

import warnings
warnings.filterwarnings("ignore")

import time
import ccxt
import requests
from datetime import datetime
from logger_util import info

# --- Configuración de API ---
API_KEY    = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'
PAIR       = 'BTC/USD:USD'
TIMEFRAME  = '1h'
MA_PERIOD  = 20
STD_DEV    = 2
SIZE       = 0.01

# --- Telegram ---
TELEGRAM_TOKEN   = '8127892735:AAGZDT0xOzLpV01ISH5LKN3wG7INEL12Xrk'
TELEGRAM_CHAT_ID = '925121458'

def telegram(msg):
    try:
        text = f"[BOLLINGER 1H] 📈 {msg}"
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={'chat_id': TELEGRAM_CHAT_ID, 'text': text},
            timeout=10
        )
    except Exception as e:
        info('telegram', f"❌ Error enviando mensaje: {e}")

# --- Inicializar cliente CCXT ---
exchange = ccxt.krakenfutures({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
})
exchange.load_markets()

# --- Funciones principales ---
def fetch_ohlcv():
    info('data', f"Cargando datos OHLCV ({TIMEFRAME})…")
    return exchange.fetch_ohlcv(PAIR, timeframe=TIMEFRAME, limit=MA_PERIOD + 2)


def calc_bollinger(ohlcv):
    closes = [candle[4] for candle in ohlcv]
    window = closes[-MA_PERIOD:]
    ma = sum(window) / MA_PERIOD
    var = sum((p - ma) ** 2 for p in window) / MA_PERIOD
    std = var ** 0.5
    upper = ma + STD_DEV * std
    lower = ma - STD_DEV * std
    info('cycle', f"Bandas: μ={ma:.2f}, +{STD_DEV}σ={upper:.2f}, -{STD_DEV}σ={lower:.2f}")
    return upper, lower


def check_signal(price, upper, lower):
    if price >= upper:
        return 'sell'
    if price <= lower:
        return 'buy'
    return None


def place_order(side, price):
    info('signal', f"Señal de {side.upper()}. Enviando orden…")
    telegram(f"🚨 Señal de {side.upper()} detectada. Colocando orden LIMIT @ {price:.2f}")
    order = exchange.create_order(
        symbol=PAIR,
        type='limit',
        side=side,
        amount=SIZE,
        price=price,
        params={'postOnly': False, 'timeInForce': 'GTC'}
    )
    info('order', f"id={order['id']}, price={order['price']}, qty={order['amount']}")
    telegram(f"✅ Orden {side.upper()} colocada: id={order['id']} @ {order['price']:.2f}")


def sleep_until_next_candle():
    now = datetime.now()
    seconds = 3600 - now.minute * 60 - now.second
    info('sleep', f"Durmiendo {seconds}s hasta próximo cierre de vela 1h")
    time.sleep(seconds)


def run_cycle():
    ohlcv = fetch_ohlcv()
    upper, lower = calc_bollinger(ohlcv)
    last_price = ohlcv[-1][4]
    side = check_signal(last_price, upper, lower)
    if not side:
        info('no_signal', "Sin señal o ya tradeado hoy.")
        return
    price = upper if side == 'sell' else lower
    place_order(side, price)


def main():
    info('start', "BOLLINGER 1H activo. Esperando cierre de vela…")
    telegram("🟢 Sistema BOLLINGER 1H activado y operativo.")
    while True:
        try:
            run_cycle()
        except Exception as e:
            info('error', f"Error en ciclo: {e}")
            telegram(f"❌ Error en ciclo: {e}")
        sleep_until_next_candle()


if __name__ == '__main__':
    main()
