#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import warnings
from datetime import datetime, timedelta

import ccxt
import pandas as pd
import numpy as np
import pytz
import requests
from trade_logger import log_trade

# === SEGURIDAD: LOCKFILE ===

# === SEGURIDAD: LOCKFILE AUTOLIMPIABLE ===
LOCKFILE = "/tmp/vol_eth.lock"

if os.path.exists(LOCKFILE):
    try:
        with open(LOCKFILE, 'r') as f:
            pid = int(f.read())
        os.kill(pid, 0)
        print("Ya hay una instancia activa. Saliendo.")
        sys.exit(0)
    except:
        print("Lockfile encontrado, pero el proceso no existe. Eliminando lockfile.")
        os.remove(LOCKFILE)

# Escribimos nuestro PID actual
with open(LOCKFILE, "w") as f:
    f.write(str(os.getpid()))


# === CONFIG ===
API_KEY        = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET     = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'
TELEGRAM_TOKEN = '8127892735:AAGZDT0xOzLpV01ISH5LKN3wG7INEL12Xrk'
TELEGRAM_CHAT  = '925121458'

SYMBOL          = 'ETH/USD:USD'
TIMEFRAME       = '4h'
POSITION_SIZE   = 1
MAX_LONG        = 5
MAX_SHORT       = 5
EXCLUDED_DAYS   = [1]     # Martes
EXCLUDED_MONTHS = [9]     # Septiembre

ICONS = {
    "buy": "🟢", "sell": "🔴", "error": "❌",
    "no_signal": "🔎", "active": "🛠", "heartbeat": "✅",
    "start": "▶️", "wait": "🕒"
}

tz = pytz.timezone('Europe/Madrid')
exchange = ccxt.krakenfutures({'apiKey': API_KEY, 'secret': API_SECRET})
exchange.load_markets()

def now(fmt="%d/%m %H:%M"):
    return datetime.now(tz).strftime(fmt)

def seconds_until_next_4h():
    now_dt = datetime.now(tz)
    next_hour = ((now_dt.hour // 4) + 1) * 4
    if next_hour >= 24:
        next_dt = (now_dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        next_dt = now_dt.replace(hour=next_hour, minute=0, second=0, microsecond=0)
    return (next_dt - now_dt).total_seconds()

def send_telegram(msg):
    print(f"Telegram: {msg}")
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={'chat_id': TELEGRAM_CHAT, 'text': msg})
    except:
        pass

def EMA(series, period):
    return series.ewm(span=period).mean()

def ATR(df, period=14):
    tr = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift()),
        abs(df['low'] - df['close'].shift())
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def MACD_line(series, fast=12, slow=26):
    return EMA(series, fast) - EMA(series, slow)

def RSI(series, period=13):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    return 100 - (100 / (1 + gain.rolling(period).mean() / loss.rolling(period).mean()))

def ROC(series, n=14):
    return (series - series.shift(n)) / series.shift(n) * 100

def fetch_data():
    try:
        exchange.load_markets()
        if SYMBOL not in exchange.markets:
            raise ValueError(f"Símbolo {SYMBOL} no disponible en Kraken Futures.")
        ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=300)
        df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms').dt.tz_localize('UTC')
        df.set_index('timestamp', inplace=True)
        return df
    except Exception as e:
        send_telegram(f"{ICONS['error']} Error al obtener datos: {e}")
        raise
    ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=300)
    df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms').dt.tz_localize('UTC')
    df.set_index('timestamp', inplace=True)
    return df

def check_signals(df):
    now_dt = datetime.now(tz)
    if now_dt.month in EXCLUDED_MONTHS or now_dt.weekday() in EXCLUDED_DAYS:
        return None
    if now_dt.hour % 4 != 0:
        return None

    close = df['close']
    atr   = ATR(df, 14)
    macd  = MACD_line(close)
    rsi   = RSI(close, 13)
    roc   = ROC(close, 14)

    ema5, ema10, ema15 = EMA(close,5), EMA(close,10), EMA(close,15)
    ema60, ema200      = EMA(close,60), EMA(close,200)

    macd_up   = (macd > macd.shift(1)) & (macd > 0)
    macd_down = (macd < macd.shift(1)) & (macd < 0)

    atr_chan_up   = close.rolling(25).mean() + 1.4*atr
    atr_chan_down = close.rolling(25).mean() - 1.4*atr

    bullish = (ema5>ema10) & (atr>30) & (rsi>70) & macd_up & (close>atr_chan_up)
    bearish = (ema5<ema10) & (atr>20) & (rsi<28) & macd_down & (close<atr_chan_down)

    long_cond  = bullish.iloc[-1] and ema15.iloc[-1]>ema60.iloc[-1] and close.iloc[-1]>ema200.iloc[-1]
    short_cond = bearish.iloc[-1] and ema15.iloc[-1]<ema60.iloc[-1] and close.iloc[-1]<ema200.iloc[-1]

    price = close.iloc[-1]
    sl    = float(atr.iloc[-1]) * 3.5
    tp    = sl * 3.5

    return {"long": long_cond, "short": short_cond, "price": price, "sl": sl, "tp": tp}

def place_order(side, price, sl, tp):
    try:
        if side == "long":
            exchange.create_market_buy_order(SYMBOL, POSITION_SIZE)
            icon, action = ICONS['buy'], "COMPRA"
        else:
            exchange.create_market_sell_order(SYMBOL, POSITION_SIZE)
            icon, action = ICONS['sell'], "VENTA"

        msg = f"{icon} {action} ejecutada – ETH @ {price:.2f} | SL {sl:.2f} | TP {tp:.2f} – {now()}"
        print(msg)
        send_telegram(msg)
        send_telegram(f"{ICONS[side]} Orden ejecutada: {side.upper()} en {SYMBOL}")
        log_trade("Vol_ETH", side, price, sl, tp)
    except Exception as e:
        err = f"{ICONS['error']} Error orden: {e}"
        print(err)
        send_telegram(err)

# === BUCLE PRINCIPAL ===
if __name__ == "__main__":
    try:
        ts = now()
        print(f"{ICONS['start']} Vol_ETH ciclo iniciado: {ts}")
        send_telegram(f"{ICONS['active']} Vol_ETH activo ⏰ {ts}")
        next_hb = datetime.now(tz) + timedelta(hours=1)

        while True:
            try:
                if datetime.now(tz) >= next_hb:
                    send_telegram(f"{ICONS['heartbeat']} Bot activo – {now('%H:%M')}")
                    next_hb += timedelta(hours=1)

                df = fetch_data()
                signals = check_signals(df)

                if signals:
                    if signals['long']:
                        place_order('long',  signals['price'], signals['sl'], signals['tp'])
                    elif signals['short']:
                        place_order('short', signals['price'], signals['sl'], signals['tp'])
                    else:
                        send_telegram(f"{ICONS['no_signal']} Vol_ETH sin señales claras.")
                else:
                    send_telegram(f"{ICONS['no_signal']} Vol_ETH sin ejecución por bloqueo.")

                print(f"{ICONS['wait']} Esperando 4h...")
                time.sleep(seconds_until_next_4h())
            except Exception as e:
                err = f"{ICONS['error']} Error inesperado: {e}"
                print(err)
                send_telegram(err)
                time.sleep(60)
    finally:
        if os.path.exists(LOCKFILE):
            os.remove(LOCKFILE)


def trailing_stop_price(entry_price, direction, atr, factor=0.5):
    if direction == "buy":
        return entry_price - factor * atr
    else:
        return entry_price + factor * atr
