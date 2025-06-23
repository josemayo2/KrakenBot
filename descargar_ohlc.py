import requests
import time
import csv
from datetime import datetime

def fetch_ohlc_data(pair, interval, since=None):
    url = 'https://api.kraken.com/0/public/OHLC'
    params = {
        'pair': pair,
        'interval': interval
    }
    if since:
        params['since'] = since
    response = requests.get(url, params=params)
    data = response.json()
    return data

def save_to_csv(data, filename):
    with open(filename, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['datetime', 'open', 'high', 'low', 'close'])
        for entry in data:
            timestamp = datetime.fromtimestamp(entry[0])
            open_price = entry[1]
            high = entry[2]
            low = entry[3]
            close = entry[4]
            writer.writerow([timestamp, open_price, high, low, close])

# Configuración
pair = 'XRPEUR'
interval = 15  # ⛔ Kraken NO permite intervalos de 10 min, usamos 15

# Obtener datos
print("Descargando datos de OHLC...")
ohlc_data = fetch_ohlc_data(pair, interval)

# Verificación
if 'error' in ohlc_data and ohlc_data['error']:
    print("❌ Error de la API de Kraken:", ohlc_data['error'])
    exit()

# Extraer datos del par
result = ohlc_data['result']
pair_key = list(result.keys())[0]
data = result[pair_key]

# Guardar en CSV
filename = 'ohlc_15min.csv'
save_to_csv(data, filename)
print(f"✅ Datos guardados en {filename}")
