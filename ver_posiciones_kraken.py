import ccxt
import os
from dotenv import load_dotenv

# Cargar claves
load_dotenv("/Users/jose/Documents/KrakenBot/.env")
api_key = os.getenv("KRAKEN_API_KEY")
api_secret = os.getenv("KRAKEN_API_SECRET")

exchange = ccxt.krakenfutures({
    'apiKey': api_key,
    'secret': api_secret,
    'enableRateLimit': True,
})

positions = exchange.privateGetOpenpositions()
print("📊 Posiciones abiertas en Kraken Futures:")
for pos in positions['openPositions']:
    symbol = pos['symbol']
    size = pos['size']
    entry = pos['entryPrice']
    side = pos['side']
    print(f"🔹 {symbol} | {side.upper()} | {size} BTC @ {entry} USD")
