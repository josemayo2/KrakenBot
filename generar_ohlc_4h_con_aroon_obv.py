
import ccxt
import pandas as pd
import time

def fetch_ohlcv(symbol='BTC/USD:PI', timeframe='4h', limit=5000):
    exchange = ccxt.krakenfutures()
    ohlcv = exchange.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    return df

def compute_aroon(df, period=14):
    high = df['high'].rolling(window=period+1)
    low = df['low'].rolling(window=period+1)
    aroon_up = high.apply(lambda x: float(period - x[::-1].argmax()) / period * 100, raw=False)
    aroon_down = low.apply(lambda x: float(period - x[::-1].argmin()) / period * 100, raw=False)
    return aroon_up, aroon_down

def compute_obv(df):
    obv = [0]
    for i in range(1, len(df)):
        if df['close'].iloc[i] > df['close'].iloc[i - 1]:
            obv.append(obv[-1] + df['volume'].iloc[i])
        elif df['close'].iloc[i] < df['close'].iloc[i - 1]:
            obv.append(obv[-1] - df['volume'].iloc[i])
        else:
            obv.append(obv[-1])
    return pd.Series(obv, index=df.index)

def main():
    print("Descargando datos OHLC 4h para BTC/USD (Futures)...")
    df = fetch_ohlcv()
    print("Calculando indicadores Aroon y OBV...")
    df['Aroon_Up'], df['Aroon_Down'] = compute_aroon(df)
    df['OBV'] = compute_obv(df)
    filename = 'ohlc_4h_btcusd_aroon_obv.csv'
    df.to_csv(filename)
    print(f"✅ Archivo guardado como {filename}")

if __name__ == "__main__":
    main()
