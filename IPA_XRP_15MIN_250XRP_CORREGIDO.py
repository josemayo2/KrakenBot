
import pandas as pd
import time
import matplotlib.pyplot as plt
from datetime import datetime

def leer_datos_ohlc(ruta_csv):
    df = pd.read_csv(ruta_csv)
    df['datetime'] = pd.to_datetime(df['datetime'])
    df.set_index('datetime', inplace=True)
    return df

def calcular_sma(df, periodos):
    for periodo in periodos:
        df[f'SMA{periodo}'] = df['close'].rolling(window=periodo).mean()
    return df

def detectar_senal(df):
    if df.empty or len(df) < 13:
        return None
    ult = df.iloc[-1]
    sma_vals = [ult['SMA5'], ult['SMA8'], ult['SMA13']]
    if None in sma_vals or any(pd.isna(sma_vals)):
        return None
    if sma_vals == sorted(sma_vals):
        return 'COMPRA'
    elif sma_vals == sorted(sma_vals, reverse=True):
        return 'VENTA'
    return None

def mostrar_estado(precio, sma5, sma8, sma13, señal):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Precio: {precio:.5f} | SMA5: {sma5:.5f}, SMA8: {sma8:.5f}, SMA13: {sma13:.5f} | Señal: {señal or 'NINGUNA'}")

csv_file = 'ohlc_15min.csv'
print("[Sistema] En funcionamiento... esperando condiciones.")

while True:
    try:
        df = leer_datos_ohlc(csv_file)
        df = calcular_sma(df, [5, 8, 13])
        señal = detectar_senal(df)
        if señal:
            ult = df.iloc[-1]
            mostrar_estado(ult['close'], ult['SMA5'], ult['SMA8'], ult['SMA13'], señal)
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Esperando SMA completa o sin condiciones.")
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Error: {e}")
    time.sleep(900)  # 15 minutos
