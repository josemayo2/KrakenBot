
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
from datetime import datetime
import pytz
import ccxt
import pandas as pd
import numpy as np
import requests
from trade_logger import log_trade

ICONS = {
    "no_signal": "❔",
    "buy":       "🟢",
    "sell":      "🔴",
    "active":    "⚡",
    "sleep":     "😴",
    "error":     "⚠️",
    "heartbeat": "✅",
    "retry":     "🔄",
}

tz = pytz.timezone('Europe/Madrid')
def now(fmt="%H:%M:%S"):
    return datetime.now(tz).strftime(fmt)

API_KEY        = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET     = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'
TELEGRAM_TOKEN = '8127892735:AAGZDT0xOzLpV01ISH5LKN3wG7INEL12Xrk'
TELEGRAM_CHAT  = '925121458'

SYMBOL          = 'BTC/USD:USD'
TIMEFRAME       = '5m'
POSITION_SIZE   = 0.05
EMA_PERIOD      = 550
RSI_PERIOD      = 12
RSI_LOW         = 16
RSI_HIGH        = 88

EXCLUDED_DAYS   = [5, 7]     # Viernes, Domingo
EXCLUDED_MONTHS = [9]        # Solo septiembre
START_HOUR      = 6
END_HOUR        = 22

exchange = ccxt.krakenfutures({'apiKey': API_KEY, 'secret': API_SECRET})
exchange.load_markets()

# === Control de acumulación ===
MAX_LONG = 5
MAX_SHORT = 5
COUNTER_FILE = 'rsi_position_counter.txt'

def read_counters():
    if not os.path.exists(COUNTER_FILE):
        return {"LONG": 0, "SHORT": 0}
    with open(COUNTER_FILE, 'r') as f:
        lines = f.read().strip().splitlines()
    counts = {"LONG": 0, "SHORT": 0}
    for line in lines:
        key, value = line.split("=")
        counts[key.strip()] = int(value)
    return counts

def write_counters(counts):
    with open(COUNTER_FILE, 'w') as f:
        for k, v in counts.items():
            f.write(f"{k}={v}\n")


BOT_TAG = "[RSI]"

def send_telegram(message):
    mensaje_final = f"{BOT_TAG} {message}"
    if mensaje_final.startswith('[RSI] ✅'):
        mensaje_final += ' – RSI Bot iniciado en Kraken Futures'
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT, "text": mensaje_final})
    except Exception as e:
        print(f"{ICONS['error']} {now()} Error Telegram: {e}")
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT, "text": message})
    except Exception as e:
        print(f"{ICONS['error']} {now()} Error Telegram: {e}")

def write_log(message):
    timestamp = now()
    with open("RSI_log.txt", "a") as f:
        f.write(f"[{timestamp}] {message}\n")

def fetch_data():
    ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=EMA_PERIOD + 100)
    df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms').dt.tz_localize('UTC').dt.tz_convert('Europe/Madrid')
    return df

def compute_indicators(df):
    ha = (df['open'] + df['high'] + df['low'] + df['close']) / 4
    df['heikin_close'] = ha
    df['ema'] = ha.ewm(span=EMA_PERIOD).mean()

    delta = ha.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/RSI_PERIOD, min_periods=RSI_PERIOD).mean()
    avg_loss = loss.ewm(alpha=1/RSI_PERIOD, min_periods=RSI_PERIOD).mean()
    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))
    return df

def trading_allowed(now_dt):
    return (
        START_HOUR <= now_dt.hour < END_HOUR and
        now_dt.isoweekday() not in EXCLUDED_DAYS and
        now_dt.month not in EXCLUDED_MONTHS
    )

def get_open_positions():
    positions = exchange.private_get_openpositions()['openPositions']
    longs = sum(1 for p in positions if p['type'] == 'buy' and p['symbol'] == SYMBOL)
    shorts = sum(1 for p in positions if p['type'] == 'sell' and p['symbol'] == SYMBOL)
    return longs, shorts

def get_signal(df):
    if len(df) < 2:
        return None
    now_dt = df['timestamp'].iloc[-1]
    if not trading_allowed(now_dt):
        return None
    prev, curr = df['rsi'].iloc[-2], df['rsi'].iloc[-1]
    hc, ema = df['heikin_close'].iloc[-1], df['ema'].iloc[-1]
    if hc > ema and prev <= RSI_LOW < curr:
        return 'long'
    if hc < ema and prev >= RSI_HIGH > curr:
        return 'short'
    return None

def place_order(signal):
    try:
        longs, shorts = get_open_positions()
        if signal == 'long' and longs >= 5:
            return
        if signal == 'short' and shorts >= 5:
            return
        df = compute_indicators(fetch_data())
        price = df['heikin_close'].iloc[-1]
        if signal == 'long':
            exchange.create_market_buy_order(SYMBOL, POSITION_SIZE)
            icon = ICONS['buy']
        else:
            exchange.create_market_sell_order(SYMBOL, POSITION_SIZE)
            icon = ICONS['sell']
        msg = f"{icon} {signal.upper()} ejecutada en {SYMBOL} @ {price:.2f} – {now()}"
        send_telegram(msg)
        write_log(msg)
        if side == 'buy': counts['LONG'] += 1
        elif side == 'sell': counts['SHORT'] += 1
        write_counters(counts)
        log_trade("RSI_kraken", signal, price)
    except Exception as e:
        err = f"Error orden: {e}"
        send_telegram(f"{ICONS['error']} {err}")
        write_log(err)

def wait_until_next_5min():
    while True:
        now_dt = datetime.now(pytz.timezone("Europe/Madrid"))
        if now_dt.minute % 5 == 0 and now_dt.second < 5:
            break
        time.sleep(1)

def run_bot():
    hb_time = time.time()
    send_telegram(f"{ICONS['active']} Bot RSI iniciado – {now()}")
    write_log("Bot iniciado")
    while True:
        try:
            wait_until_next_5min()
            df = compute_indicators(fetch_data())
            sig = get_signal(df)
            if sig:
                place_order(sig)
            else:
                if time.time() - hb_time >= 900:
                    hb = f"{ICONS['heartbeat']} Bot activo, sin señales – {now()}"
                    send_telegram(hb)
                    write_log(hb)
                    hb_time = time.time()
                else:
                    print(f"{ICONS['sleep']} {now()} No hay señal")
        except Exception as e:
            err = f"Error general: {e}"
            send_telegram(f"{ICONS['error']} {err}")
            write_log(err)
        time.sleep(1)

if __name__ == '__main__':
    while True:
        try:
            run_bot()
        except Exception as e:
            err = f"Bot reiniciado tras error: {e}"
            send_telegram(f"{ICONS['retry']} {err} – {now()}")
            write_log(err)
            time.sleep(10)
