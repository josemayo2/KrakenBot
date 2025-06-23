#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ichimoku_bot.py

Bot Ichimoku para Kraken Futures con CCXT, Telegram y gestión de TP/SL con trailing.
Incluye:
- Prevención de instancias múltiples
- Mensaje de inicio y health-check inicial
- Heartbeat cada 6 horas
"""
import os
import sys
import warnings
import urllib3
import time
from datetime import datetime, timedelta
import pytz
import ccxt
import pandas as pd
import requests
from typing import List, Optional

# -------------------------------------------------------------------
# Previene instancias múltiples con PIDFILE
PIDFILE = f"/tmp/{os.path.basename(__file__)}.pid"
try:
    fd = os.open(PIDFILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
except FileExistsError:
    print("Otra instancia del bot ya está corriendo.")
    sys.exit(1)
import atexit
@atexit.register
def _cleanup_pidfile():
    try:
        os.remove(PIDFILE)
    except OSError:
        pass
# -------------------------------------------------------------------

# Silencia warnings de SSL/LibreSSL
warnings.filterwarnings("ignore")
urllib3.disable_warnings(urllib3.exceptions.NotOpenSSLWarning)

# --------------------------------------------------------------
# Configuración global
timezone        = pytz.timezone('Europe/Madrid')
SYMBOL          = 'BTC/USD:USD'      # Kraken Futures ticker
INTERVAL_MIN    = 1                  # ciclo cada 1 minuto

# Gestión de riesgo
TAKE_PROFIT_PCT = 0.0135             # 1.35%
STOP_LOSS_PCT   = 0.0150             # 1.50%
TRAIL_START_PCT = 0.0046             # 0.46% inicio trailing

# Heartbeat
PING_INTERVAL   = timedelta(hours=6)

# Credenciales (mantén este fichero seguro)
API_KEY         = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET      = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'
TELEGRAM_TOKEN  = '8127892735:AAGZDT0xOzLpV01ISH5LKN3wG7INEL12Xrk'
TELEGRAM_CHAT   = '925121458'
TELEGRAM_URL    = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'

# Cliente Kraken Futures
exchange = ccxt.krakenfutures({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
})

# Estado de posición para trailing
position = {'side': None, 'entry': 0.0, 'stop': 0.0}

# --------------------------------------------------------------
# Helpers
def log(msg: str):
    ts = datetime.now(timezone).strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] {msg}")

def send_telegram(msg: str):
    try:
        requests.post(TELEGRAM_URL,
                      json={'chat_id': TELEGRAM_CHAT, 'text': msg},
                      timeout=10)
    except Exception as e:
            raw = str(e)
            if '504' in raw:
                err = '504 Gateway Time-out'
            else:
                err = raw.splitlines()[0]
            log(f"⚠️ {tf} intento {i+1}/5: {err}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            backoff = min(backoff * 2, 60)
    log(f"❌ OHLCV fallo {tf}")
    return None

# --------------------------------------------------------------
# Cálculo Ichimoku
def ichimoku_df(df: pd.DataFrame) -> pd.DataFrame:
    h, l, c = df['high'], df['low'], df['close']
    tenkan = (h.rolling(9).max() + l.rolling(9).min()) / 2
    kijun  = (h.rolling(26).max() + l.rolling(26).min()) / 2
    spanA  = ((tenkan + kijun) / 2).shift(26)
    spanB  = ((h.rolling(52).max() + l.rolling(52).min()) / 2).shift(26)
    df['tenkan'] = tenkan
    df['kijun']  = kijun
    df['A']      = spanA
    df['B']      = spanB
    return df.dropna()

# --------------------------------------------------------------
# Obtención de señales
def get_signals() -> (bool, bool, float):
    dfs = {}
    for tf in ['1m', '5m', '15m']:
        data = fetch_ohlcv(tf)
        if not data:
            return False, False, 0.0
        df = pd.DataFrame(data, columns=['time','open','high','low','close','vol'])
        df['time'] = pd.to_datetime(df['time'], unit='ms')
        df.set_index('time', inplace=True)
        ich = ichimoku_df(df)
        if len(ich) < (2 if tf=='1m' else 1):
            log(f"⚠️ No suficientes datos Ichimoku en {tf}, len={len(ich)}")
            return False, False, 0.0
        dfs[tf] = ich

    l1, p1 = dfs['1m'].iloc[-1], dfs['1m'].iloc[-2]
    l5  = dfs['5m'].iloc[-1]
    l15 = dfs['15m'].iloc[-1]

    bull = lambda r: r['close'] > max(r['A'], r['B'])
    bear = lambda r: r['close'] < min(r['A'], r['B'])

    co = p1['close'] <= p1['kijun'] and l1['close'] > l1['kijun']
    cu = p1['close'] >= p1['kijun'] and l1['close'] < l1['kijun']
    b1 = bull(l1) and co and l1['close'] > l1['tenkan']
    s1 = bear(l1) and cu and l1['close'] < l1['tenkan']

    buy  = bull(l15) and bull(l5) and b1
    sell = bear(l15) and bear(l5) and s1

    return buy, sell, l1['close']

# --------------------------------------------------------------
# --------------------------------------------------------------
# Bucle principal
def main():
    # Health-check inicial
    log("🚀 Bot arranca...")
    try:
        ticker = exchange.fetch_ticker(SYMBOL)
        log(f"📡 Salud inicial {SYMBOL} bid={ticker['bid']:.2f} ask={ticker['ask']:.2f}")
        send_telegram(f"✅ Ichimoku Bot iniciado. {SYMBOL} bid {ticker['bid']:.2f}/ask {ticker['ask']:.2f}")
    except Exception as e:
        log(f"❌ Error salud inicial: {e}")
        send_telegram(f"❌ Error inicial con {SYMBOL}: {str(e).splitlines()[0]}")
        sys.exit(1)

    last_ping = datetime.now(timezone)

    while True:
        now = datetime.now(timezone)
        # Heartbeat
        if now - last_ping > PING_INTERVAL:
            send_telegram("💓 Bot Ichimoku sigue vivo y operativo.")
            last_ping = now

        if not (valid_time(now) and valid_day(now)):
            time.sleep(60)
            continue

        buy, sell, price = get_signals()

        # TRAILING
        if position['side']=='buy' and price >= position['entry']*(1+TRAIL_START_PCT):
            new_sl = price*(1-STOP_LOSS_PCT)
            if new_sl > position['stop']:
                position['stop'] = new_sl
                log(f"🔄 Trail LONG SL -> {new_sl:.2f}")
        if position['side']=='sell' and price <= position['entry']*(1-TRAIL_START_PCT):
            new_sl = price*(1+STOP_LOSS_PCT)
            if position['stop']==0 or new_sl < position['stop']:
                position['stop'] = new_sl
                log(f"🔄 Trail SHORT SL -> {new_sl:.2f}")

        # NUEVAS SEÑALES (solo si no hay posición abierta)
        if buy and not position['side']:
            # Definir cuántos contratos quieres abrir (puede ser fraccional)
            CONTRACTS = 0.15  # <--- Ajusta aquí la cantidad de contratos (ej. 0.15) <--- Ajusta el número de contratos
            tp = price*(1+TAKE_PROFIT_PCT)
            sl = price*(1-STOP_LOSS_PCT)
            position.update({'side':'buy','entry':price,'stop':sl})
            # Ejecutar orden de mercado (descomenta si quieres operar)
            # exchange.create_market_buy_order(SYMBOL, CONTRACTS)
            msg = f"🟢 BUY {SYMBOL} x{CONTRACTS} @ {price:.2f}" {SYMBOL} x{CONTRACTS} @ {price:.2f}
🎯 TP: {tp:.2f}
❌ SL: {sl:.2f}"
            log(msg.replace("
"," "))
            send_telegram(msg)

        if sell and not position['side']:
            CONTRACTS = 1  # <--- Ajusta el número de contratos
            tp = price*(1-TAKE_PROFIT_PCT)
            sl = price*(1+STOP_LOSS_PCT)
            position.update({'side':'sell','entry':price,'stop':sl})
            # Ejecutar orden de mercado (descomenta si quieres operar)
            # exchange.create_market_sell_order(SYMBOL, CONTRACTS)
            msg = f"🔴 SELL {SYMBOL} x{CONTRACTS} @ {price:.2f}
🎯 TP: {tp:.2f}
❌ SL: {sl:.2f}"
            log(msg.replace("
"," "))
            send_telegram(msg)

        # CIERRE AUTOMÁTICO
        if position['side']=='buy' and (price>=position['entry']*(1+TAKE_PROFIT_PCT) or price<=position['stop']):
            log(f"✅ Cierra LONG @ {price:.2f}")
            position.update({'side':None,'entry':0.0,'stop':0.0})
        if position['side']=='sell' and (price<=position['entry']*(1-TAKE_PROFIT_PCT) or price>=position['stop']):
            log(f"✅ Cierra SHORT @ {price:.2f}")
            position.update({'side':None,'entry':0.0,'stop':0.0})

        time.sleep(INTERVAL_MIN * 60)

if __name__ == '__main__':
    main()
    main()
