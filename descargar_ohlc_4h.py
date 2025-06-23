
import ccxt
import csv
from datetime import datetime

# Configuración
symbol = 'PI_XBTUSD'  # BTC/USD Futures en Kraken
timeframe = '4h'
filename = f"ohlc_4h_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

# Crear instancia del exchange
exchange = ccxt.krakenfutures()

# Descargar datos OHLC
print("📥 Descargando datos OHLC 4h de Kraken Futures...")
ohlcv = exchange.fetch_ohlcv(symbol, timeframe)

# Guardar a CSV
with open(filename, mode='w', newline='') as file:
    writer = csv.writer(file)
    writer.writerow(['timestamp', 'datetime', 'open', 'high', 'low', 'close', 'volume'])
    for candle in ohlcv:
        ts = candle[0]
        dt = datetime.utcfromtimestamp(ts / 1000).strftime('%Y-%m-%d %H:%M:%S')
        writer.writerow([ts, dt] + candle[1:])

print(f"✅ Datos OHLC 4h guardados en {filename}")
