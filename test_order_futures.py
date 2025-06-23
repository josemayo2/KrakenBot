import ccxt
import time

# ✅ CLAVES DE FUTURES
api_key = "Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb"
api_secret = "gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d"

# ⚙️ Configuración Kraken Futures
exchange = ccxt.krakenfutures({
    'apiKey': api_key,
    'secret': api_secret,
    'enableRateLimit': True,
})

symbol = "BTC/USD:USD"
amount = 0.001

# === OPERACIÓN DE PRUEBA ===
try:
    print("🧪 Conectando y lanzando orden de prueba...")

    # Crea orden de compra al mercado
    order = exchange.create_market_buy_order(symbol=symbol, amount=amount)
    print("✅ Orden ejecutada:")
    print(order)

    # Espera 2 segundos
    time.sleep(2)

    # Cancela orden por si aún está abierta (por seguridad)
    order_id = order['id']
    exchange.cancel_order(order_id, symbol)
    print("🚫 Orden cancelada (si seguía activa)")

except Exception as e:
    print("❌ Error durante la prueba:", e)
