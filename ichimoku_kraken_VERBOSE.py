
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import ccxt
import pandas as pd
import numpy as np
import time
import requests
from datetime import datetime, timedelta
import pytz
import os
import warnings
warnings.filterwarnings("ignore")

# === CONFIGURACIÓN API y TELEGRAM ===
API_KEY        = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET     = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'
TELEGRAM_TOKEN = '8127892735:AAGZDT0xOzLpV01ISH5LKN3wG7INEL12Xrk'
TELEGRAM_CHAT  = '925121458'

SYMBOL         = 'BTC/USD:USD'
TIMEFRAME      = '1m'
ORDER_SIZE     = 0.03
STOP_PERCENT   = 0.015
TP_PERCENT     = 0.0135
TRAILING_PERC  = 0.0046

EXCLUDED_WEEKDAYS = [4]  # 0=Lunes ... 4=Viernes
EXCLUDED_MONTHS_LONG = [5]  # Excluir largos en mayo
MIN_HOUR = 70000
MAX_HOUR = 201000
BLOCK_HOURS = [(140000, 172700), (80000, 83000)]

VERBOSE = True

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=data)
    except:
        pass

def get_ohlcv():
    exchange = ccxt.krakenfutures({
        'apiKey': API_KEY,
        'secret': API_SECRET,
    })
    since = exchange.milliseconds() - 1000 * 60 * 500
    ohlcv = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, since=since)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

def is_time_allowed(now):
    t = int(now.strftime('%H%M%S'))
    if not (MIN_HOUR <= t <= MAX_HOUR):
        return False
    for start, end in BLOCK_HOURS:
        if start <= t <= end:
            return False
    return True

def check_verbose(now, direction, time_ok, day_ok, month_ok, candle_ok):
    if VERBOSE:
        msg = f"🕐 [{now.strftime('%H:%M')}] Evaluando entrada {direction}...
"
        msg += f"   {'✅' if time_ok else '⛔'} Time OK | "
        msg += f"{'✅' if day_ok else '⛔'} Día OK | "
        msg += f"{'✅' if month_ok else '⛔'} Mes OK | "
        msg += f"{'✅' if candle_ok else '⛔'} Filtro vela 5m"
        print(msg)

def main():
    print("⚡ Bot Ichimoku iniciado — modo verbose activo")
    send_telegram("⚡ *Bot Ichimoku activo* — modo verbose iniciado")
    while True:
        now = datetime.now(pytz.timezone('Europe/Madrid'))
        df = get_ohlcv()

        weekday_ok = now.weekday() not in EXCLUDED_WEEKDAYS
        month_ok_long = now.month not in EXCLUDED_MONTHS_LONG
        time_ok = is_time_allowed(now)

        last_candle = df.iloc[-1]
        close = last_candle['close']
        open_ = last_candle['open']

        candle_ok = abs(close - open_) / open_ > 0.0001

        check_verbose(now, "LONG", time_ok, weekday_ok, month_ok_long, candle_ok)

        # Simulación para testeo: no ejecutamos orden
        time.sleep(60)

if __name__ == '__main__':
    main()
