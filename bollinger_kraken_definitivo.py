
import time
import ccxt
import requests
from datetime import datetime

API_KEY        = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET     = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'
TELEGRAM_TOKEN = '8127892735:AAGZDT0xOzLpV01ISH5LKN3wG7INEL12Xrk'
TELEGRAM_CHAT  = '925121458'
BOT_TAG        = "[BOLL]"

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"❌ Error enviando mensaje Telegram: {e}")

# Mensaje de arranque
now = datetime.now().strftime('%d/%m %H:%M:%S')
send_telegram(f"{BOT_TAG} ✅ Bot activo, sin señales – {now} – BOLL Bot iniciado en Kraken Futures")

# Inicializar exchange
exchange = ccxt.krakenfutures({
    'apiKey': API_KEY,
    'secret': API_SECRET,
})

symbol = 'BTC/USD:USD'
timeframe = '1h'
order_size = 0.07
sleep_interval = 60 * 60  # 1 hora

def fetch_data():
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe)
        return ohlcv
    except Exception as e:
        send_telegram(f"{BOT_TAG} ❌ Error al obtener datos: {e}")
        return None

def signal_generator(data):
    if not data or len(data) < 20:
        return ""
    closes = [c[4] for c in data]
    ma = sum(closes[-20:]) / 20
    std = (sum((x - ma) ** 2 for x in closes[-20:]) / 20) ** 0.5
    upper = ma + 2 * std
    lower = ma - 2 * std
    last = closes[-1]
    if last > upper:
        return "sell"
    elif last < lower:
        return "buy"
    return ""

def place_order(signal):
    try:
        side = 'buy' if signal == 'buy' else 'sell'
        order = exchange.create_market_order(symbol, side, order_size)
        send_telegram(f"{BOT_TAG} ✅ Orden {side.upper()} ejecutada: {order}")
    except Exception as e:
        send_telegram(f"{BOT_TAG} ❌ Error ejecutando orden {signal}: {e}")

while True:
    data = fetch_data()
    signal = signal_generator(data)
    if signal:
        send_telegram(f"{BOT_TAG} 📉 Señal detectada: {signal.upper()}")
        place_order(signal)
    else:
        print(f"{BOT_TAG} Esperando 1h...")
    time.sleep(sleep_interval)
