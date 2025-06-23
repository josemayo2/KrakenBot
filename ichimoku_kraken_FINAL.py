
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import pytz
import ccxt
import requests
import pandas as pd
from datetime import datetime, timedelta

# === CONFIGURACIÓN API & TELEGRAM ===
API_KEY        = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET     = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'
TELEGRAM_TOKEN = '8127892735:AAGZDT0xOzLpV01ISH5LKN3wG7INEL12Xrk'
TELEGRAM_CHAT  = '925121458'

# === PARÁMETROS ===
SYMBOL            = 'BTC/USD:USD'
POSITION_SIZE     = 0.03
STOP_PERCENT      = 0.015
PROFIT_PERCENT    = 0.0135
TRAILING_PERCENT  = 0.0046
MAX_POSITIONS     = 5
MIN_TIME_BETWEEN  = 3600  # segundos
EXCLUDED_DAYS     = [5]   # Viernes
EXCLUDED_MONTHS_L = [5]   # Mayo (solo largos)

tz = pytz.timezone('Europe/Madrid')
def now(fmt="%H:%M:%S"):
    return datetime.now(tz).strftime(fmt)

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT, "text": message})
    except:
        pass

def get_time_constraints():
    dt = datetime.now(tz)
    if dt.weekday() in EXCLUDED_DAYS:
        return False
    if not (7 <= dt.hour < 20 or (dt.hour == 20 and dt.minute <= 10)):
        return False
    if 14 <= dt.hour < 17 or (dt.hour == 17 and dt.minute <= 27):
        return False
    if 8 <= dt.hour < 8 or (dt.hour == 8 and dt.minute <= 30):
        return False
    return True

def fetch_data():
    ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe='1m', limit=200)
    df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms').dt.tz_localize('UTC').dt.tz_convert(tz)
    return df

def ichimoku_signal(df):
    high9 = df['high'].rolling(window=9).max()
    low9 = df['low'].rolling(window=9).min()
    tenkan_sen = (high9 + low9) / 2

    high26 = df['high'].rolling(window=26).max()
    low26 = df['low'].rolling(window=26).min()
    kijun_sen = (high26 + low26) / 2

    current_close = df['close'].iloc[-1]
    ts, ks = tenkan_sen.iloc[-1], kijun_sen.iloc[-1]

    if ts > ks and current_close > ts:
        return "long"
    if ts < ks and current_close < ts:
        return "short"
    return None

def can_trade(direction, last_trades):
    now_time = datetime.now(tz)
    last_time = last_trades.get(direction)
    if last_time and (now_time - last_time).total_seconds() < MIN_TIME_BETWEEN:
        return False
    return True

exchange = ccxt.krakenfutures({'apiKey': API_KEY, 'secret': API_SECRET})
exchange.load_markets()
last_trades = {'long': None, 'short': None}

def get_open_positions():
    try:
        positions = exchange.private_get_openpositions()['openPositions']
        long_count = sum(1 for p in positions if p['type'] == 'buy' and p['symbol'] == SYMBOL)
        short_count = sum(1 for p in positions if p['type'] == 'sell' and p['symbol'] == SYMBOL)
        return long_count, short_count
    except:
        return 0, 0

def place_order(direction, price):
    msg = ""
    if direction == 'long':
        exchange.create_market_buy_order(SYMBOL, POSITION_SIZE)
        sl = price * (1 - STOP_PERCENT)
        tp = price * (1 + PROFIT_PERCENT)
    else:
        exchange.create_market_sell_order(SYMBOL, POSITION_SIZE)
        sl = price * (1 + STOP_PERCENT)
        tp = price * (1 - PROFIT_PERCENT)

    msg = f"✅ Orden ejecutada ({direction.upper()}) – {POSITION_SIZE} BTC\nTP: {tp:.2f}, SL: {sl:.2f}, Trailing: {TRAILING_PERCENT*100:.2f}%"
    send_telegram(msg)
    last_trades[direction] = datetime.now(tz)

send_telegram("⚡ Bot Ichimoku iniciado")

while True:
    try:
        if get_time_constraints():
            df = fetch_data()
            signal = ichimoku_signal(df)
            if signal:
                long_count, short_count = get_open_positions()
                if signal == 'long' and (datetime.now(tz).month in EXCLUDED_MONTHS_L):
                    continue
                if signal == 'long' and long_count < MAX_POSITIONS and can_trade('long', last_trades):
                    price = df['close'].iloc[-1]
                    place_order('long', price)
                if signal == 'short' and short_count < MAX_POSITIONS and can_trade('short', last_trades):
                    price = df['close'].iloc[-1]
                    place_order('short', price)
        time.sleep(60)
    except Exception as e:
        send_telegram(f"⚠️ Error: {e}")
        time.sleep(60)
