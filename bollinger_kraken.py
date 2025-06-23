#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bollinger_kraken_BOLL_REAL.py

Bot Bollinger operando en real para Kraken Futures
"""

import os
import time
import ccxt
import pandas as pd
import requests
from datetime import datetime, timezone

# === CREDENCIALES Y PARÁMETROS ===
API_KEY         = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET      = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'
TELEGRAM_TOKEN  = '8127892735:AAGZDT0xOzLpV01ISH5LKN3wG7INEL12Xrk'
TELEGRAM_CHAT   = '925121458'

SYMBOL          = 'BTC/USD:USD'
TIMEFRAME       = '1h'
LIMIT           = 100
ORDER_SIZE      = 0.07
SLEEP_INTERVAL  = 3600  # segundos
BOT_TAG         = "[BOLL]"

# === CONEXIÓN A KRAKEN FUTURES ===
exchange = ccxt.krakenfutures({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
})
exchange.load_markets()

last_action = None  # Para evitar operaciones duplicadas

def send_telegram(message):
    mensaje_final = f"{BOT_TAG} {message}"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT,
        'text': mensaje_final
    }
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"Error enviando mensaje Telegram: {e}")

def get_ohlcv():
    bars = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=LIMIT)
    df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

def check_signal(df):
    df['ma'] = df['close'].rolling(window=20).mean()
    df['std'] = df['close'].rolling(window=20).std()
    df['upper'] = df['ma'] + 2 * df['std']
    df['lower'] = df['ma'] - 2 * df['std']

    last = df.iloc[-1]
    if last['close'] > last['upper']:
        return 'sell'
    elif last['close'] < last['lower']:
        return 'buy'
    else:
        return ''

def execute_trade(direction):
    side = 'buy' if direction == 'buy' else 'sell'
    try:
        order = exchange.create_market_order(SYMBOL, side, ORDER_SIZE)
        send_telegram(f"✅ Orden ejecutada: {side.upper()} {ORDER_SIZE} en {SYMBOL}")
    except Exception as e:
        send_telegram(f"❌ Error al ejecutar orden: {e}")

def main():
    global last_action
    send_telegram("🤖 Bot Bollinger operativo en Kraken Futures (REAL)")
    while True:
        try:
            df = get_ohlcv()
            signal = check_signal(df)
            timestamp = df['timestamp'].iloc[-1].isoformat()

            if signal and last_action != (signal, timestamp):
                send_telegram(f"📉 Señal detectada: {signal.upper()} en {SYMBOL} ({TIMEFRAME})")
                execute_trade(signal)
                last_action = (signal, timestamp)
            else:
                print(f"{datetime.now().isoformat()} - Sin nueva señal.")
        except Exception as e:
            send_telegram(f"⚠️ Error: {e}")
        time.sleep(SLEEP_INTERVAL)

if __name__ == '__main__':
    main()
