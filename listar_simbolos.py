import ccxt
import os
from dotenv import load_dotenv

load_dotenv("/Users/jose/Documents/KrakenBot/.env")

exchange = ccxt.krakenfutures({
    'apiKey': os.getenv("KRAKEN_API_KEY"),
    'secret': os.getenv("KRAKEN_API_SECRET"),
    'enableRateLimit': True,
})

exchange.load_markets()

print("=== SÍMBOLOS DISPONIBLES EN KRAKEN FUTURES ===\n")
for symbol in exchange.symbols:
    market = exchange.market(symbol)
    print(f"{symbol:20} | ID: {market['id']} | Precision: {market['precision']['amount']}")
