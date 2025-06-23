import ccxt
import time
import pandas as pd
import requests
from datetime import datetime

# API Credentials
API_KEY = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'

# Telegram bot credentials
TELEGRAM_TOKEN = '8127892735:AAGZDT0xOzLpV01ISH5LKN3wG7INEL12Xrk'
TELEGRAM_CHAT_ID = '925121458'

# Configuration
exchange = ccxt.krakenfutures({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
})
# Símbolo para futuros de Bitcoin en Kraken
symbol = 'BTC/USD:USD'
timeframe = '1h'

# Strategy parameters
STOP_PERCENT     = 1.15    # initial stop loss in %
TP_PERCENT       = 2.21    # initial take profit in %
TRAIL_PERCENT    = 0.60    # trailing percent
NVELAS           = 7    # bars before halving stops/trailing
CONTRACT_SIZE    = 0.1     # tamaño de la posición en BTC

# State variables
pos_open           = False
entry_index        = None
new_sl_price       = None
entry_price        = None
last_entry_attempt = None  # índice de último intento de entrada

# Telegram helper
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': message}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"Telegram error: {e}")

# Logging with Telegram
def log_and_telegram(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    full = f"[{ts}] {msg}"
    print(full)
    send_telegram(full)

# Fetch with retry/backoff to avoid 504s
def fetch_ohlcv_with_retry(max_retries=3, backoff=5):
    for attempt in range(1, max_retries+1):
        try:
            data = exchange.fetch_ohlcv(symbol, timeframe)
            return data
        except Exception as e:
            log_and_telegram(f"⚠️ Fetch attempt {attempt} failed: {e}")
            if attempt < max_retries:
                time.sleep(backoff * attempt)
            else:
                raise

# Build DataFrame and indicators
def load_and_indicate():
    raw = fetch_ohlcv_with_retry()
    df = pd.DataFrame(raw, columns=['timestamp','open','high','low','close','volume'])
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('datetime', inplace=True)
    # Indicators
    df['MA31'] = df['close'].rolling(31).mean()
    df['MA53'] = df['close'].rolling(53).mean()
    df['MA1215'] = df['close'].rolling(1215).mean()
    df['BB_mid'] = df['close'].rolling(5).mean()
    df['BB_std'] = df['close'].rolling(5).std()
    df['BB_low'] = df['BB_mid'] - df['BB_std']
    df['BB_high'] = df['BB_mid'] + df['BB_std']
    df['pB'] = (df['close'] - df['BB_low'])/(df['BB_high'] - df['BB_low'])
    return df

# Entry logic
def place_entry(df, idx):
    global pos_open, entry_index, new_sl_price, entry_price, last_entry_attempt
    try:
        price = df['close'].iloc[idx]
        entry_price = price
        log_and_telegram(f"🟢 BUY Entry on {symbol} at {price:.2f}")
        exchange.create_market_buy_order(symbol, CONTRACT_SIZE)
        # Take Profit (maker)
        tp = price * (1 + TP_PERCENT/100)
        exchange.create_order(symbol, 'takeProfitLimit', 'sell', CONTRACT_SIZE, tp,
                               {'stopPrice': tp, 'reduceOnly': True, 'timeInForce': 'GTC'})
        log_and_telegram(f"🎯 TP set at {tp:.2f}")
        # Stop Loss (maker)
        sl = price * (1 - STOP_PERCENT/100)
        exchange.create_order(symbol, 'stopLossLimit', 'sell', CONTRACT_SIZE, sl,
                               {'stopPrice': sl, 'reduceOnly': True, 'timeInForce': 'GTC'})
        log_and_telegram(f"🛑 SL set at {sl:.2f}")
        pos_open = True; entry_index = idx; new_sl_price = None; last_entry_attempt = None
    except Exception as e:
        last_entry_attempt = idx
        log_and_telegram(f"❌ Entry error at idx {idx}: {e}")

# Manage existing position
def manage_position(df, idx):
    global pos_open, entry_index, new_sl_price, entry_price
    price = df['close'].iloc[idx]
    bars = idx - entry_index
    # adjust percentages
    sp = STOP_PERCENT/2 if bars > NVELAS else STOP_PERCENT
    tr = TRAIL_PERCENT/2 if bars > NVELAS else TRAIL_PERCENT
    trail_start = entry_price * (tr/100)
    # break-even
    if new_sl_price is None and price - entry_price >= trail_start:
        new_sl_price = entry_price
        log_and_telegram(f"🔒 BE SL activated at {new_sl_price:.2f}")
    # trailing
    if new_sl_price is not None and price - new_sl_price >= trail_start:
        new_sl_price = price - trail_start
        exchange.cancel_all_orders(symbol)
        exchange.create_order(symbol, 'stopLossLimit', 'sell', CONTRACT_SIZE, new_sl_price,
                               {'stopPrice': new_sl_price, 'reduceOnly': True, 'timeInForce': 'GTC'})
        log_and_telegram(f"🔒 Trailing SL moved to {new_sl_price:.2f}")
    # exit by pB
    if df['pB'].iloc[idx-1] < 1 <= df['pB'].iloc[idx]:
        log_and_telegram(f"🔴 SELL Exit on pB at {price:.2f}")
        exchange.create_market_sell_order(symbol, CONTRACT_SIZE)
        pos_open=False; entry_index=None; new_sl_price=None

# Main check
def run():
    log_and_telegram("▶️ Running latest-bar check")
    df = load_and_indicate()
    latest = len(df) - 1
    global last_entry_attempt
    if not pos_open:
        if last_entry_attempt == latest: return
        ca1 = df['close'].iloc[latest] > df['MA31'].iloc[latest]
        ca2 = df['MA53'].iloc[latest] > df['MA1215'].iloc[latest]
        ca3 = df['pB'].iloc[latest-1] < 0.18 and df['pB'].iloc[latest] < 0.00
        if ca1 and ca2 and ca3: place_entry(df, latest)
    else:
        manage_position(df, latest)

if __name__ == '__main__':
    log_and_telegram("🚀 Starting bollinger2_kraken.py with retry/backoff")
    while True:
        try:
            run()
        except Exception as e:
            log_and_telegram(f"❗️ Fatal error: {e}")
        time.sleep(15 * 60)  # 15 minutos entre cada check
