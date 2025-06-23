import ccxt
import os
from dotenv import load_dotenv

load_dotenv("/Users/jose/Documents/KrakenBot/.env")

api_key = os.getenv("KRAKEN_API_KEY")
api_secret = os.getenv("KRAKEN_API_SECRET")

exchange = ccxt.krakenfutures({
    'apiKey': api_key,
    'secret': api_secret,
    'enableRateLimit': True,
})

try:
    balance = exchange.fetch_balance()
    print("✅ Conexión OK. Balance:", balance['total'])
except Exception as e:
    print("❌ Error:", e)
