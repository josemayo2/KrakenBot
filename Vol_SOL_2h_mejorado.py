#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import ccxt
import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime, timedelta, timezone
import pytz
import os

LOCKFILE = "/tmp/vol_sol.lock"
if os.path.exists(LOCKFILE):
    try:
        with open(LOCKFILE, 'r') as f:
            pid = int(f.read())
        os.kill(pid, 0)
        print("Ya hay una instancia activa. Saliendo.")
        exit(0)
    except:
        print("Lockfile encontrado, pero el proceso no existe. Eliminando lockfile.")
        os.remove(LOCKFILE)
with open(LOCKFILE, "w") as f:
    f.write(str(os.getpid()))

API_KEY = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'
TELEGRAM_TOKEN = '8127892735:AAGZDT0xOzLpV01ISH5LKN3wG7INEL12Xrk'
TELEGRAM_CHAT_ID = '925121458'
SYMBOL = 'SOL/USD:USD'
POSITION_SIZE = 10
MAX_LONG = 5
MAX_SHORT = 5
COUNTER_FILE = 'position_counter.txt'
EXCLUDED_MONTHS = []
EXCLUDED_DAYS = []

BOT_TAG = "[VOL_SOL]"

exchange = ccxt.krakenfutures({'apiKey': API_KEY, 'secret': API_SECRET, 'enableRateLimit': True})

def send_telegram(message):
    mensaje_final = f"{BOT_TAG} {message}"
    print(f"📩 Telegram: {mensaje_final}")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": mensaje_final})
    except Exception as e:
        print(f"❌ Error enviando Telegram: {e}")

def fetch_data():
    ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe='1h', limit=500)
    df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    return df

def resample_2h(df):
    return df.resample('2h').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna()

def EMA(series, period): return series.ewm(span=period).mean()
def ATR(df, period): return pd.concat([
    df['high'] - df['low'],
    abs(df['high'] - df['close'].shift()),
    abs(df['low'] - df['close'].shift())
], axis=1).max(axis=1).rolling(window=period).mean()
def MACD_line(series, fast=12, slow=26): return EMA(series, fast) - EMA(series, slow)
def RSI(series, period):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    rs = gain.rolling(window=period).mean() / loss.rolling(window=period).mean()
    return 100 - (100 / (1 + rs))

def log_trade(side, price, sl, tp):
    with open("all_Trades.csv", "a") as f:
        f.write(f"{datetime.now()},{SYMBOL},{side},{price:.2f},{sl:.2f},{tp:.2f}\n")


def hay_posicion_abierta():
    try:
        posiciones = exchange.fetch_positions([SYMBOL])
        for p in posiciones:
            if p['contracts'] > 0:
                return True
        return False
    except Exception as e:
        send_telegram(f"❌ Error al comprobar posición abierta: {e}")
        return False

def read_counters():
    if not os.path.exists(COUNTER_FILE): return {"LONG": 0, "SHORT": 0}
    with open(COUNTER_FILE, 'r') as f:
        lines = f.read().strip().splitlines()
    counts = {"LONG": 0, "SHORT": 0}
    for line in lines:
        key, value = line.split("=")
        counts[key.strip()] = int(value.strip())
    return counts

def write_counters(counters):
    with open(COUNTER_FILE, 'w') as f:
        for key in ["LONG", "SHORT"]:
            f.write(f"{key}={counters[key]}\n")

def reset_counters_if_needed():
    try:
        positions = exchange.fetch_positions()
        long_count = sum(p['contracts'] > 0 and p['side'].upper() == 'LONG' and p['symbol'] == SYMBOL for p in positions)
        short_count = sum(p['contracts'] > 0 and p['side'].upper() == 'SHORT' and p['symbol'] == SYMBOL for p in positions)
        write_counters({"LONG": long_count, "SHORT": short_count})
    except Exception as e:
        send_telegram(f"⚠️ Error al comprobar posiciones: {e}")

def check_signals(df):
    now = datetime.now(timezone.utc)
    if now.month in EXCLUDED_MONTHS or now.weekday() in EXCLUDED_DAYS:
        return None

    close = df['close']
    ema_fast, ema_slow = EMA(close, 2), EMA(close, 5)
    ema_conf_fast, ema_conf_slow = EMA(close, 5), EMA(close, 60)
    ema_trend = EMA(close, 2)

    adx, atr = ATR(df, 10), ATR(df, 1)
    rsi = RSI(close, 11)
    macd, macd_prev = MACD_line(close), MACD_line(close).shift(1)
    macd_up, macd_down = (macd > macd_prev) & (macd > 0), (macd < macd_prev) & (macd < 0)

    atr_channel_up = close.rolling(50).mean() + 0.5 * atr
    atr_channel_down = close.rolling(10).mean() - 1.65 * atr
    valid_trend = (close > atr_channel_up) | (close < atr_channel_down)

    bullish = (ema_fast > ema_slow) & (adx > 5) & (rsi > 71)
    bearish = (ema_fast < ema_slow) & (adx > 20) & (rsi < 32)
    confirm_long = ema_conf_fast > ema_conf_slow
    confirm_short = ema_conf_fast < ema_conf_slow
    trend_filter_long = close > ema_trend
    trend_filter_short = close < ema_trend

    sl = atr.iloc[-1] * 5.85
    tp = sl * 2.15
    price = close.iloc[-1]

    long_signal = (bullish & macd_up & (close > ema_fast) & valid_trend & confirm_long & trend_filter_long).iloc[-1]
    short_signal = (bearish & macd_down & (close < ema_slow) & valid_trend & confirm_short & trend_filter_short).iloc[-1]

    return {"long": long_signal, "short": short_signal, "sl": sl, "tp": tp, "price": price}

def place_order(side, price, sl, tp):
    counters = read_counters()
    if side == "long" and counters["LONG"] >= MAX_LONG:
        send_telegram(f"🚫 Límite {MAX_LONG} posiciones LONG alcanzado.")
        return
    if side == "short" and counters["SHORT"] >= MAX_SHORT:
        send_telegram(f"🚫 Límite {MAX_SHORT} posiciones SHORT alcanzado.")
        return

    try:
        if side == "long":
            exchange.create_market_buy_order(SYMBOL, POSITION_SIZE)
            counters["LONG"] += 1
            op_side = "sell"
            icon = "🟢📈 COMPRA"
        else:
            exchange.create_market_sell_order(SYMBOL, POSITION_SIZE)
            counters["SHORT"] += 1
            op_side = "buy"
            icon = "🔴📉 VENTA"

        write_counters(counters)
        msg = f"{icon} ejecutada\nSOL FUTURES @ {price:.2f}\n🎯 SL: {sl:.2f} | TP: {tp:.2f}"
        print(msg)
        send_telegram(f"✅ {msg}")
        log_trade(side, price, sl, tp)

        sl_price = int(price - sl) if side == "long" else int(price + sl)
        tp_price = int(price + tp) if side == "long" else int(price - tp)

        exchange.create_order(SYMBOL, 'stop market', op_side, POSITION_SIZE, params={'stopPrice': sl_price})
        exchange.create_order(SYMBOL, 'take_profit market', op_side, POSITION_SIZE, params={'price': tp_price})

        send_telegram(f"🎯 SL colocado en {sl_price} y TP en {tp_price}")

    except Exception as e:
        send_telegram(f"❌ Error orden: {e}")

def sleep_until_next_even_hour():
    now = datetime.now()
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    if next_hour.hour % 2 != 0:
        next_hour += timedelta(hours=1)
    time.sleep((next_hour - now).total_seconds())

if __name__ == "__main__":
    while True:
        try:
            now_str = datetime.now().strftime('%d/%m %H:%M')
            print(f"▶️ Vol_SOL_2h ciclo iniciado: {now_str}")
            send_telegram(f"🛠 Vol_SOL_2h activo ⏰ {now_str}")
            reset_counters_if_needed()
            df = resample_2h(fetch_data())
            signals = check_signals(df)
            if signals:
                if signals["long"]:
                    place_order("long", signals["price"], signals["sl"], signals["tp"])
                elif signals["short"]:
                    place_order("short", signals["price"], signals["sl"], signals["tp"])
                else:
                    send_telegram("🔎 Vol_SOL_2h sin señales claras.")
            else:
                send_telegram("⏳ Vol_SOL_2h sin ejecución por bloqueo.")
        except Exception as e:
            send_telegram(f"❌ Error inesperado: {e}")
        print("🕒 Esperando 2h exactas...")
        sleep_until_next_even_hour()
