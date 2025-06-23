
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import ccxt
import requests
import pandas as pd
from datetime import datetime
import pytz

# === CONFIGURACIÓN API Y TELEGRAM ===
API_KEY        = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET     = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'
TELEGRAM_TOKEN = '8127892735:AAGZDT0xOzLpV01ISH5LKN3wG7INEL12Xrk'
TELEGRAM_CHAT  = '925121458'

SYMBOL = 'BTC/USD:USD'
TIMEFRAME = '1m'
ORDER_SIZE = 0.03
STOPPERCENT = 1.5
PROFITPERCENT = 1.35
TRAILINGPERCENT = 0.46

MAX_POSITIONS_SAME_DIRECTION = 5
position_counter = {"long": 0, "short": 0}
direction = None

timezone = pytz.timezone("Europe/Madrid")
exchange = ccxt.krakenfutures({
    'apiKey': API_KEY,
    'secret': API_SECRET,
})
exchange.load_markets()

def send_telegram(msg):
    try:
        requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                     params={"chat_id": TELEGRAM_CHAT, "text": msg})
    except Exception as e:
        print(f"Error Telegram: {e}")

def fetch_ohlcv(symbol, timeframe='1m', limit=150):
    data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(data, columns=['timestamp','open','high','low','close','volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

def get_ichimoku_signal(df):
    df['tenkan'] = (df['high'].rolling(9).max() + df['low'].rolling(9).min()) / 2
    df['kijun'] = (df['high'].rolling(26).max() + df['low'].rolling(26).min()) / 2
    if df['tenkan'].iloc[-1] > df['kijun'].iloc[-1]:
        return 'long'
    elif df['tenkan'].iloc[-1] < df['kijun'].iloc[-1]:
        return 'short'
    else:
        return None

def time_filters(now):
    t = int(now.strftime('%H%M%S'))
    m = int(now.strftime('%m'))
    d = now.weekday()
    if d == 4 or (m == 5 and direction == 'long'):
        return False
    if t < 70000 or t > 201000 or (140000 <= t <= 172700) or (80000 <= t <= 83000):
        return False
    return True

def place_order(signal):
    global position_counter
    if signal == 'long':
        if position_counter['long'] >= MAX_POSITIONS_SAME_DIRECTION:
            return
        exchange.create_market_buy_order(SYMBOL, ORDER_SIZE)
        position_counter['long'] += 1
        position_counter['short'] = 0
    elif signal == 'short':
        if position_counter['short'] >= MAX_POSITIONS_SAME_DIRECTION:
            return
        exchange.create_market_sell_order(SYMBOL, ORDER_SIZE)
        position_counter['short'] += 1
        position_counter['long'] = 0

while True:
    now = datetime.now(timezone)
    try:
        df = fetch_ohlcv(SYMBOL, TIMEFRAME)
        direction = get_ichimoku_signal(df)
        print(f"🕐 [{now.strftime('%H:%M')}] Evaluando entrada {direction}...")

        if direction and time_filters(now):
            send_telegram(f"📥 [{now.strftime('%H:%M')}] Señal activa: {direction.upper()} en {SYMBOL}")
            place_order(direction)
        else:
            print(f"⏳ [{now.strftime('%H:%M')}] Sistema activo – sin señal")

        time.sleep(60)

    except Exception as e:
        send_telegram(f"⚠️ [{now.strftime('%H:%M')}] ERROR: {e}")
        time.sleep(60)
