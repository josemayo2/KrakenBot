
import requests
import csv
from datetime import datetime

def fetch_ohlc_data(pair, interval):
    url = 'https://futures.kraken.com/derivatives/api/v3/ohlc'
    params = {
        'symbol': pair,
        'interval': interval
    }
    response = requests.get(url, params=params)
    return response.json()

def save_to_csv(data, filename):
    with open(filename, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        for entry in data:
            writer.writerow([
                datetime.utcfromtimestamp(entry[0] / 1000),
                entry[1], entry[2], entry[3], entry[4], entry[5]
            ])

# Configuración
pair = 'PI_XBTUSD'
interval = 240  # 4 horas

print("Descargando datos de OHLC 4h para BTC/USD...")
ohlc_data = fetch_ohlc_data(pair, interval)

if 'result' in ohlc_data and 'candles' in ohlc_data['result']:
    candles = ohlc_data['result']['candles']
    save_to_csv(candles, 'ohlc_4h_btcusd.csv')
    print("✅ Datos guardados en ohlc_4h_btcusd.csv")
else:
    print("❌ Error en la descarga:", ohlc_data)
