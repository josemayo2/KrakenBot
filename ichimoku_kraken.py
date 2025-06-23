
# ichimoku_kraken_REAL_OK_v6.py

# === CONFIGURACIÓN INICIAL ===
SYMBOL = 'BTC/USD:USD'
ORDER_SIZE = 0.03  # BTC
TIMEFRAME = '1m'
MAX_TRADES = 5

# === CLAVES REALES INCLUIDAS ===
API_KEY = 'Bs5X+...'
API_SECRET = 'gBvwa...'
TELEGRAM_TOKEN = '8127892735:...'
TELEGRAM_CHAT = '925121458'

# === LÓGICA DE ENTRADA CON FILTRO DE CRUCE REAL ===
# Solo genera señal si hay cruce Tenkan > Kijun en esta vela (no si ya estaba cruzado)
prev_tenkan = tenkan_sen.iloc[-2]
    prev_kijun = kijun_sen.iloc[-2]
    prev_tenkan = tenkan_sen.iloc[-2]
    prev_kijun = kijun_sen.iloc[-2]
if prev_tenkan <= prev_kijun and tenkan > kijun:
    if long_trades < MAX_TRADES and not in_position:
        # ENTRADA EN LARGO
        place_market_order('buy', ORDER_SIZE)
        in_position = True
        long_trades += 1
        send_telegram("🔔 Señal Ichimoku ➕ LONG ejecutada – {symbol} – {qty} BTC")

# === MENSAJE DE TELEGRAM SIMPLIFICADO ===
# Solo una línea con estado actual si no opera
send_telegram("✅ Ichimoku activo. Sin señal.")  # si no se cumple ninguna entrada

# === CONTROL DE ESTADO DE POSICIÓN (simulado o vía Kraken) ===
# Verifica si hay posiciones abiertas antes de permitir nuevas entradas

# === EJECUCIÓN CONTINUA O CADA MINUTO CON CONTROL DE ESTADO ===
