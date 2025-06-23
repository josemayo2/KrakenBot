
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import ccxt
import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime, timedelta
import pytz

# === CONFIGURACIÓN API y PARÁMETROS ===
API_KEY        = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET     = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'
TELEGRAM_TOKEN = '8127892735:AAGZDT0xOzLpV01ISH5LKN3wG7INEL12Xrk'
TELEGRAM_CHAT  = '925121458'
SYMBOL         = 'BTC/USD:USD'
TIMEFRAME      = '1m'
POSITION_SIZE  = 0.03
STOP_PERCENT   = 1.5
TP_PERCENT     = 1.35
TRAILING_PERCENT = 0.46

def send_telegram(msg):
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                  data={"chat_id": TELEGRAM_CHAT, "text": msg})

exchange = ccxt.krakenfutures({
    'apiKey': API_KEY,
    'secret': API_SECRET,
})

def fetch_ohlcv():
    now = int(time.time()) * 1000
    since = now - 1000 * 60 * 200
    ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, since=since)
    df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

def compute_signal(df):
    now = datetime.now(pytz.timezone("Europe/Madrid"))
    if now.weekday() == 4:  # Viernes
        return None
    if now.hour < 7 or now.hour > 20 or (14 <= now.hour < 17 and now.minute < 27):
        return None
    if now.month == 5:
        allow_long = False
    else:
        allow_long = True

    # Indicador RSI simplificado
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    df['rsi'] = rsi

    last_rsi = df['rsi'].iloc[-1]
    if last_rsi < 30 and allow_long:
        return 'buy'
    elif last_rsi > 70:
        return 'sell'
    else:
        return None

def execute_trade(signal, price):
    if signal == 'buy':
        side = 'buy'
        sl = price * (1 - STOP_PERCENT / 100)
        tp = price * (1 + TP_PERCENT / 100)
        ts = TRAILING_PERCENT
    elif signal == 'sell':
        side = 'sell'
        sl = price * (1 + STOP_PERCENT / 100)
        tp = price * (1 - TP_PERCENT / 100)
        ts = TRAILING_PERCENT
    else:
        return

    order = exchange.create_order(
        symbol=SYMBOL,
        type='market',
        side=side,
        amount=POSITION_SIZE,
        params={}
    )
    send_telegram(f"✅ Orden ejecutada ({side.upper()}) – {POSITION_SIZE} BTC\nTP: {tp:.2f}, SL: {sl:.2f}, Trailing: {ts:.2f}%")

# === MAIN LOOP ===
send_telegram("✅ Bot RSI LIMITED activo en Kraken Futures")
while True:
    try:
        df = fetch_ohlcv()
        signal = compute_signal(df)
        if signal:
            price = df['close'].iloc[-1]
            send_telegram(f"📊 Señal detectada: {signal.upper()} en {SYMBOL} ({TIMEFRAME})")
            execute_trade(signal, price)
        else:
            print("⏳ No hay señal")
        time.sleep(60)
    except Exception as e:
        send_telegram(f"❌ Error: {str(e)}")
        time.sleep(60)
