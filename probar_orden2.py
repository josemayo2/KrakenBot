import ccxt
import time

API_KEY    = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'.strip()
API_SECRET = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'.strip()
SYMBOL     = 'BTC/USD:USD'  # Asegúrate de que este string salga en fetch_markets()
AMOUNT     = 0.01

exchange = ccxt.krakenfutures({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
})

# Cargamos los mercados una sola vez
exchange.load_markets()

try:
    # 1) Verificar autenticación leyendo balance
    bal = exchange.fetch_balance({ 'type': 'future' })
    print("✅ Balance autenticado:", bal['total'])

    # 2) Enviar orden de mercado
    order = exchange.create_market_buy_order(SYMBOL, AMOUNT)
    print("➡️ Orden enviada OK:", order)

    # -----------------------------
    # Ya no hacemos fetch_order ni fetch_orders aquí, 
    # porque KrakenFutures no lo soporta con fetch_order(...) 
    # y queremos evitar errores posteriores.
    # Si quieres ver órdenes cerradas, podrías hacerlo en otro script.
    # -----------------------------

except Exception as e:
    # Solo reportamos errores aquí si algo falla al crear la orden.
    print("❌ Error al autenticar o crear orden:", e)
