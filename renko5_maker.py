#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
renko5_maker.py
Renko5 Maker for Kraken Futures:
- Market entries puros (no reduce_only)
- SL/TP/Trailing con reduce_only=True
- Máx. 5 entradas por side
- 2 bricks consecutivos
- Reset de contadores tras 120s y cierre real
- ATR(14) filter: ATR >= BOX_SIZE/2
- Cooldown 60s
- Telegram ASCII-only
"""

import ccxt
import time
import requests
import re
from datetime import datetime

# === CREDENTIALS ===
API_KEY          = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET       = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'
TELEGRAM_TOKEN   = '8127892735:AAGZDT0xOzLpV01ISH5LKN3wG7INEL12Xrk'
TELEGRAM_CHAT_ID = '925121458'

# === PARAMETERS ===
SYMBOL               = 'BTC/USD:USD'
ORDER_SIZE           = 0.002
BOX_SIZE             = 20.0
STOP_LOSS_PCT        = 0.01
TAKE_PROFIT_PCT      = 0.011
TRAILING_START_PCT   = 0.0049
TRAILING_STEP_PCT    = 0.001    # callbackRate = 0.1%
MAX_ENTRIES_PER_SIDE = 5
COOLDOWN             = 60       # seconds
RESET_DELAY          = 120      # seconds
ATR_PERIOD           = 14       # minutes

# State
last_order_ts    = 0.0
brick_up_count   = 0
brick_down_count = 0
buy_entry_count  = 0
sell_entry_count = 0
position_open    = False
entry_id         = None
entry_ts         = 0.0

# === INIT EXCHANGE ===
exchange = ccxt.krakenfutures({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
})
exchange.load_markets()
if SYMBOL not in exchange.symbols:
    raise ValueError(f"{SYMBOL} not available on Kraken Futures")

# === HELPERS ===
def log(msg, notify=False):
    """Print to console with emojis; send ASCII-only to Telegram."""
    ts = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    line = f"[renko5_maker] {ts} {msg}"
    print(line)
    if notify:
        # strip non-ASCII
        ascii_line = re.sub(r'[^\x00-\x7F]', '', line)
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={'chat_id': TELEGRAM_CHAT_ID, 'text': ascii_line}
            )
        except Exception:
            pass

def clean_number(s):
    return float(re.sub(r'[^0-9\.\-]', '', str(s)) or 0)

def get_price():
    return exchange.fetch_ticker(SYMBOL)['last']

def atr(period=ATR_PERIOD):
    ohlcv = exchange.fetch_ohlcv(SYMBOL, '1m', limit=period+1)
    trs = []
    for i in range(1, len(ohlcv)):
        h, l, prev = ohlcv[i][2], ohlcv[i][3], ohlcv[i-1][4]
        trs.append(max(h-l, abs(h-prev), abs(l-prev)))
    return sum(trs) / len(trs)

def open_positions_exist():
    pos = exchange.private_get_openpositions()['openPositions']
    for p in pos:
        if p.get('symbol') == SYMBOL and clean_number(p.get('size', 0)) != 0:
            return True
    return False

def place_market_order(side):
    global last_order_ts, buy_entry_count, sell_entry_count
    global position_open, entry_id, entry_ts

    now = time.time()

    # Reset counters only after RESET_DELAY and real close
    if position_open and now - entry_ts >= RESET_DELAY and not open_positions_exist():
        position_open = False
        buy_entry_count = sell_entry_count = 0
        log("♻️ Position confirmed closed after delay, resetting counters", True)

    # Cooldown
    if now - last_order_ts < COOLDOWN:
        log(f"❄️ Cooldown active ({int(now-last_order_ts)}s<60s), skipping", False)
        return

    # Volatility filter
    current_atr = atr()
    if current_atr < BOX_SIZE/2:
        log(f"⚠️ ATR {current_atr:.2f}< {BOX_SIZE/2}, skipping", False)
        return

    # Session entry limits
    if side == 'buy' and buy_entry_count >= MAX_ENTRIES_PER_SIDE:
        log(f"⚠️ Max {MAX_ENTRIES_PER_SIDE} BUY entries reached", True)
        return
    if side == 'sell' and sell_entry_count >= MAX_ENTRIES_PER_SIDE:
        log(f"⚠️ Max {MAX_ENTRIES_PER_SIDE} SELL entries reached", True)
        return

    # MARKET entry (opens position)
    o = exchange.create_order(SYMBOL, 'market', side, ORDER_SIZE)
    price_now = get_price()
    log(f"✅ Market {side.upper()} @ {price_now:.2f} (ID {o['id']})", True)

    entry_id = o['id']
    entry_ts = now
    position_open = True
    last_order_ts = now
    if side == 'buy':
        buy_entry_count += 1
    else:
        sell_entry_count += 1

    # Stop-Loss (reduce_only)
    sl_price = round(price_now * (1-STOP_LOSS_PCT) if side=='buy' else price_now*(1+STOP_LOSS_PCT), 2)
    exchange.create_order(SYMBOL, 'stop', 'sell' if side=='buy' else 'buy',
                          ORDER_SIZE, None, {'triggerPrice': sl_price, 'reduce_only': True})
    log(f"📉 SL @ {sl_price:.2f}", True)

    # Take-Profit (reduce_only)
    tp_price = round(price_now * (1+TAKE_PROFIT_PCT) if side=='buy' else price_now*(1-TAKE_PROFIT_PCT), 2)
    exchange.create_order(SYMBOL, 'takeProfit', 'sell' if side=='buy' else 'buy',
                          ORDER_SIZE, None, {'triggerPrice': tp_price, 'reduce_only': True})
    log(f"🎯 TP @ {tp_price:.2f}", True)

    # Trailing Stop (reduce_only)
    trail_start = round(price_now * (1+TRAILING_START_PCT) if side=='buy' else price_now*(1-TRAILING_START_PCT), 2)
    callback = TRAILING_STEP_PCT * 100
    exchange.create_order(SYMBOL, 'trailingStop', 'sell' if side=='buy' else 'buy',
                          ORDER_SIZE, None, {'triggerPrice': trail_start, 'callbackRate': callback, 'reduce_only': True})
    log(f"🔃 Trailing start {trail_start:.2f}, callbackRate {callback:.3f}%", True)

# === INIT RENKO ===
price = get_price()
renko_max = price + BOX_SIZE/2
renko_min = price - BOX_SIZE/2
log("🧱 Bot RENKO5 activated", False)

# === MAIN LOOP ===
while True:
    try:
        p = get_price()
        log(f"🔄 Price {p:.2f}", False)

        if p >= renko_max:
            renko_min = renko_max
            renko_max += BOX_SIZE
            brick_up_count += 1
            brick_down_count = 0
            log("🔄 Brick UP detected", False)
            if brick_up_count >= 2:
                place_market_order('buy')
                brick_up_count = 0

        elif p <= renko_min:
            renko_max = renko_min
            renko_min -= BOX_SIZE
            brick_down_count += 1
            brick_up_count = 0
            log("🔄 Brick DOWN detected", False)
            if brick_down_count >= 2:
                place_market_order('sell')
                brick_down_count = 0

        time.sleep(60)

    except Exception as e:
        msg = str(e)
        if 'ASCII' in msg:
            print(f"[renko5_maker] 🔄 Unicode error suppressed: {msg}")
            continue
        log(f"⚠️ Main loop error: {msg}", True)
        time.sleep(15)
