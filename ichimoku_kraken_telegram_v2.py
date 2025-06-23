#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ichimoku_kraken_telegram_v3.py

Bot Ichimoku para Kraken Futures, con CCXT, clientOrderId, lógica multiframe y ciclo activo.
Incorpora:
- API y Telegram integrados directamente
- Ciclo principal con control de horario y día
- Envío de señales a Telegram
- Ejecución de órdenes en el mercado (opcionales)
- Manejo de excepciones y logs simplificados
"""
import warnings
import time
from datetime import datetime, timedelta, timezone
import pytz
import ccxt
import pandas as pd
import requests
import urllib3
from typing import Optional, List

# Silenciar advertencias
warnings.filterwarnings("ignore")
urllib3.disable_warnings(urllib3.exceptions.NotOpenSSLWarning)

# Parámetros globales
# Kraken Futures usa ticker 'BTC/USD:USD'
SYMBOL = 'BTC/USD:USD'
TIMEZONE = pytz.timezone('Europe/Madrid')
INTERVAL_MINUTES = 5

# Integración directa de claves (asegúrate de mantenerlas seguras)
KRAKEN_API_KEY = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
KRAKEN_API_SECRET = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'
TELEGRAM_TOKEN = '8127892735:AAGZDT0xOzLpV01ISH5LKN3wG7INEL12Xrk'
TELEGRAM_CHAT_ID = '925121458'
TELEGRAM_URL = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'

# CCXT Exchange: Kraken Futures
# Usa la clase krakenfutures para acceder a los mercados de derivados
exchange = ccxt.krakenfutures({
    'apiKey': KRAKEN_API_KEY,
    'secret': KRAKEN_API_SECRET,
    'enableRateLimit': True,
})


def log(msg: str):
    ts = datetime.now(TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] {msg}")


def send_telegram(text: str):
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': text}
    try:
        requests.post(TELEGRAM_URL, json=payload, timeout=10)
    except Exception as e:
        log(f"Error Telegram: {e}")


def is_valid_time(now: datetime) -> bool:
    t = int(now.strftime("%H%M%S"))
    if not (70000 <= t <= 201000):
        log("Fuera horario (07:00–20:10)")
        return False
    if 140000 <= t <= 170500:
        log("Bloqueado entre 14:00–17:05")
        return False
    return True


def is_valid_day(now: datetime, long_term: bool = False) -> bool:
    if now.weekday() == 4:
        log("Viernes: no opera")
        return False
    if long_term and now.month == 5:
        log("Mayo y largos: bloqueado")
        return False
    return True


def fetch_ohlcv(exchange: ccxt.Exchange, symbol: str, tf: str, limit: int = 1000) -> Optional[List]:
    since = exchange.parse8601(
        (datetime.now(timezone.utc) - timedelta(minutes=limit)).strftime('%Y-%m-%dT%H:%M:%SZ')
    )
    backoff = 1
    for attempt in range(5):
        try:
            return exchange.fetch_ohlcv(symbol, tf, since=since, limit=limit)
        except Exception as e:
            log(f"Error {tf} intento {attempt+1}/5: {e}")
            time.sleep(backoff)
            backoff = min(backoff*2, 60)
    log(f"Fallo persistente OHLCV {tf}")
    return None


def compute_ichimoku(df: pd.DataFrame) -> pd.DataFrame:
    high = df['high']; low = df['low']; close = df['close']
    tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
    kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
    spanA = ((tenkan + kijun) / 2).shift(26)
    spanB = ((high.rolling(52).max() + low.rolling(52).min())/2).shift(26)
    chikou = close.shift(-26)
    df['tenkan'] = tenkan; df['kijun'] = kijun
    df['spanA'] = spanA; df['spanB'] = spanB; df['chikou'] = chikou
    return df.dropna()


def to_df(data: List) -> pd.DataFrame:
    df = pd.DataFrame(data, columns=['ts','open','high','low','close','vol'])
    df['ts'] = pd.to_datetime(df['ts'], unit='ms').dt.tz_localize('UTC').dt.tz_convert(TIMEZONE)
    return df.set_index('ts')


def confirm_signals() -> (bool, bool, float):
    # Confirmar que hay suficientes datos para cada timeframe (mínimo 52+26=78 velas)
    frames = ['1m','5m','15m']; dfs = {}
    for tf in frames:
        data = fetch_ohlcv(exchange, SYMBOL, tf)
        if not data:
            log(f"No hay datos para {tf}")
            return False, False, 0.0
        df = compute_ichimoku(to_df(data))
        if len(df) < 2:
            log(f"No hay suficientes datos Ichimoku en {tf}, len={len(df)}")
            return False, False, 0.0
        dfs[tf] = df

    # Extraer últimas y previas
    try:
        last1 = dfs['1m'].iloc[-1]; prev1 = dfs['1m'].iloc[-2]
        last5 = dfs['5m'].iloc[-1]; last15 = dfs['15m'].iloc[-1]
    except IndexError:
        log("Error de índice: no hay suficientes velas para calcular señales")
        return False, False, 0.0

    def cloud_bullish(row):
        return row['close'] > max(row['spanA'], row['spanB'])
    def cloud_bearish(row):
        return row['close'] < min(row['spanA'], row['spanB'])
    
    ca15 = cloud_bullish(last15); cv15 = cloud_bearish(last15)
    ca5 = cloud_bullish(last5); cv5 = cloud_bearish(last5)

    cross_over = (prev1['close'] <= prev1['kijun'] and last1['close'] > last1['kijun'])
    cross_under = (prev1['close'] >= prev1['kijun'] and last1['close'] < last1['kijun'])

    ca1 = cloud_bullish(last1) and cross_over and last1['close'] > last1['tenkan']
    cv1 = cloud_bearish(last1) and cross_under and last1['close'] < last1['tenkan']

    buy_signal = ca15 and ca5 and ca1
    sell_signal = cv15 and cv5 and cv1
    return buy_signal, sell_signal, last1['close']
    frames = ['1m','5m','15m']; dfs = {}
    for tf in frames:
        data = fetch_ohlcv(exchange, SYMBOL, tf)
        if not data:
            return False, False, 0.0
        dfs[tf] = compute_ichimoku(to_df(data))

    last1 = dfs['1m'].iloc[-1]; prev1 = dfs['1m'].iloc[-2]
    last5 = dfs['5m'].iloc[-1]; last15 = dfs['15m'].iloc[-1]

    def cloud_bullish(row):
        return row['close'] > max(row['spanA'], row['spanB'])
    def cloud_bearish(row):
        return row['close'] < min(row['spanA'], row['spanB'])

    ca15 = cloud_bullish(last15); cv15 = cloud_bearish(last15)
    ca5 = cloud_bullish(last5); cv5 = cloud_bearish(last5)

    cross_over = (prev1['close'] <= prev1['kijun'] and last1['close'] > last1['kijun'])
    cross_under = (prev1['close'] >= prev1['kijun'] and last1['close'] < last1['kijun'])

    ca1 = cloud_bullish(last1) and cross_over and last1['close'] > last1['tenkan']
    cv1 = cloud_bearish(last1) and cross_under and last1['close'] < last1['tenkan']

    buy_signal = ca15 and ca5 and ca1
    sell_signal = cv15 and cv5 and cv1
    return buy_signal, sell_signal, last1['close']


def main():
    log("Iniciando Ichimoku Bot en Kraken Futures...")
    while True:
        now = datetime.now(TIMEZONE)
        if not is_valid_time(now) or not is_valid_day(now):
            time.sleep(60)
            continue

        buy, sell, price = confirm_signals()
        if buy:
            msg = f"Señal de COMPRA detectada en {SYMBOL} a {price:.2f}"
            log(msg); send_telegram(msg)
            # exchange.create_market_buy_order(SYMBOL, cantidad)
        elif sell:
            msg = f"Señal de VENTA detectada en {SYMBOL} a {price:.2f}"
            log(msg); send_telegram(msg)
            # exchange.create_market_sell_order(SYMBOL, cantidad)
        else:
            log("Sin señal activa.")

        time.sleep(INTERVAL_MINUTES*60)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        log("Bot detenido por usuario.")

