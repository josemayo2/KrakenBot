#!/usr/bin/env python3
import ccxt
import pandas as pd
import numpy as np
import time, os
from dotenv import load_dotenv
import ta
from datetime import datetime, timedelta
import pytz

# Carga credenciales
load_dotenv()
API_KEY    = os.getenv("KRAKENF_APIKEY")
API_SECRET = os.getenv("KRAKENF_SECRET")

# Parámetros de la estrategia
M               = 5
STOP_LOSS_PTS   = 150
TAKE_PROFIT_PTS = 600
TRAILING_PTS    = 70
ENTRY_START     = 6    # 06:00 local
ENTRY_END       = 22   # 22:00 local
POSITION_SIZE   = 0.06 # BTC por contrato

# Filtros ATR/ADX
ATR_LEN         = 14
ATR_THRESH_FAC  = 0.9
ADX_LEN         = 4
ADX_THRESHOLD   = 20

# Parámetros Koncorde
XMF_LEN         = 12
OB_UP_LEN       = 35
OB_DN_LEN       = 140
PVI_LEN         = 90
NVI_LEN         = 90
RSI_LEN         = 20
STOCH_K, STOCH_D = 14, 1

# Inicializa exchange
exchange = ccxt.krakenfutures({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
})
symbol    = 'PI_XBTUSD'
timeframe = '4h'
tz = pytz.timezone('Europe/Madrid')

def fetch_ohlcv():
    since = exchange.parse8601('2020-01-01T00:00:00Z')
    while True:
        try:
            print("📈 Cargando datos OHLCV…")
            data = exchange.fetch_ohlcv(symbol, timeframe, since, limit=1000)
            break
        except Exception as e:
            print(f"⚠️ Error fetch_ohlcv: {e}. Reintentando en 60 s…")
            time.sleep(60)
    df = pd.DataFrame(data, columns=['timestamp','open','high','low','close','volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    df.index = df.index.tz_localize('UTC').tz_convert(tz)
    return df

def calculate_indicators(df):
    print("🧮 Calculando indicadores técnicos…")
    df['pvi'] = np.nan; df['nvi'] = np.nan
    df.at[df.index[0],'pvi'] = 1000; df.at[df.index[0],'nvi'] = 1000
    for i in range(1, len(df)):
        if df['volume'].iat[i] > df['volume'].iat[i-1]:
            df['pvi'].iat[i] = df['pvi'].iat[i-1] * (df['close'].iat[i] / df['close'].iat[i-1])
        else:
            df['pvi'].iat[i] = df['pvi'].iat[i-1]
        if df['volume'].iat[i] < df['volume'].iat[i-1]:
            df['nvi'].iat[i] = df['nvi'].iat[i-1] * (df['close'].iat[i] / df['close'].iat[i-1])
        else:
            df['nvi'].iat[i] = df['nvi'].iat[i-1]

    df['pvim'] = df['pvi'].ewm(span=M).mean()
    df['oscp'] = (df['pvi'] - df['pvim']) * 100 / (df['pvim'].rolling(PVI_LEN).max() - df['pvim'].rolling(PVI_LEN).min())
    df['nvim'] = df['nvi'].ewm(span=M).mean()
    df['azul'] = (df['nvi'] - df['nvim']) * 100 / (df['nvim'].rolling(NVI_LEN).max() - df['nvim'].rolling(NVI_LEN).min())
    df['xmf']  = ta.volume.money_flow_index(close=df['close'], high=df['high'], low=df['low'], volume=df['volume'], window=XMF_LEN, fillna=True)

    up = ta.volatility.bollinger_hband(close=df['close'], window=OB_UP_LEN, fillna=True)
    dn = ta.volatility.bollinger_lband(close=df['close'], window=OB_DN_LEN, fillna=True)
    df['OB1']    = (up + dn) / 2
    df['OB2']    = up - dn
    df['BollOsc']= (df['close'] - df['OB1']) * 100 / df['OB2']

    df['xrsi'] = ta.momentum.rsi(close=df['close'], window=RSI_LEN, fillna=True)
    df['STOC'] = ta.momentum.stoch(high=df['high'], low=df['low'], close=df['close'], window=STOCH_K, smooth_window=STOCH_D, fillna=True)

    df['marron'] = (df['xrsi'] + df['xmf'] + df['BollOsc'] + df['STOC']/3) / 2
    df['verde']  = df['marron'] + df['oscp']
    df['media']  = df['marron'].ewm(span=M).mean()

    df['atr']    = ta.volatility.average_true_range(high=df['high'], low=df['low'], close=df['close'], window=ATR_LEN, fillna=True)
    df['atr_th'] = ATR_THRESH_FAC * df['atr'].rolling(50).mean()
    df['adx']    = ta.trend.adx(high=df['high'], low=df['low'], close=df['close'], window=ADX_LEN, fillna=True)

    df['hour'] = df.index.hour
    ok = (df['hour'] >= ENTRY_START) & (df['hour'] <= ENTRY_END)
    df['long_cond']  = (
        (df['verde'] > df['marron']) &
        (df['marron'] > df['media']) &
        (df['azul'] > 0.01) &
        (df['atr'] > df['atr_th']) &
        (df['adx'] > ADX_THRESHOLD) &
        ok
    )
    df['short_cond'] = (
        (df['verde'] < df['marron']) &
        (df['marron'] < df['media']) &
        (df['azul'] < 0.96) &
        (df['atr'] > df['atr_th']) &
        (df['adx'] > ADX_THRESHOLD) &
        ok
    )
    return df

def place_order(side):
    return exchange.create_order(symbol, 'market', side, POSITION_SIZE)

def main():
    position = 0
    entry_price = highest_price = lowest_price = None

    while True:
        now = datetime.now(tz)
        print(f"\n[{now.strftime('%Y-%m-%dT%H:%M:%S %Z')}] 🔄 Ejecutando ciclo 4h…")

        try:
            df = fetch_ohlcv()
            df = calculate_indicators(df)
            last = df.iloc[-1]
            signal = 'LONG' if last['long_cond'] else 'SHORT' if last['short_cond'] else 'None'
            print(f"📊 Señales evaluadas. Última señal: {signal}")

            if signal == 'None':
                print("⏳ Sin señal activa. No se envía orden.")
            else:
                print(f"🚀 Enviando orden {signal}…")
                order = place_order('buy' if signal == 'LONG' else 'sell')
                print(f"✅ Orden ejecutada a {order['average']}")
        except Exception as e:
            print(f"❌ Error durante el ciclo: {e}")

        next_hour = ((now.hour // 4) + 1) * 4
        if next_hour >= 24:
            next_candle = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        else:
            next_candle = now.replace(hour=next_hour, minute=0, second=0, microsecond=0)
        hours_to_wait = (next_candle - now).total_seconds() / 3600
        print(f"⏰ Esperando {hours_to_wait:.2f}h hasta cierre 4h: {next_candle.strftime('%Y-%m-%dT%H:%M:%S %Z')}")
        time.sleep(60)

if __name__ == "__main__":
    main()
