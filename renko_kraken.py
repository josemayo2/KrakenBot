#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
renko_kraken.py

Bot Renko para Kraken Futures con CCXT, clientOrderId y ajustes de fondos.
Incluye reintentos exponenciales en la obtención de velas.
"""
import ccxt
import time
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional

API_KEY    = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'
SYSTEM_NAME     = 'renko_kraken'
SYMBOL          = 'BTC/USD:USD'
TIMEFRAME       = '1m'
POSITION_SIZE   = 0.04
BOX_SIZE_USD    = 20.0
MAX_ORDERS      = 5
TELEGRAM_TOKEN   = '8127892735:AAGZDT0xOzLpV01ISH5LKN3wG7INEL12Xrk'
TELEGRAM_CHAT_ID = '925121458'

def send_telegram_message(text: str):
    url = 'https://api.telegram.org/bot' + TELEGRAM_TOKEN + '/sendMessage'
    data = {'chat_id': TELEGRAM_CHAT_ID, 'text': text}
    try:
        requests.post(url, data=data, timeout=10)
    except Exception:
        pass

class RenkoTrader:
    def __init__(self):
        self.exchange = ccxt.krakenfutures({
            'apiKey': API_KEY,
            'secret': API_SECRET,
            'enableRateLimit': True,
        })
        self.symbol = SYMBOL
        self.timeframe = TIMEFRAME
        self.position_size = POSITION_SIZE
        self.ValueX = BOX_SIZE_USD
        self.renkoMax = None
        self.renkoMin = None
        self.long_count = 0
        self.short_count = 0
        self.max_orders = MAX_ORDERS

    def _now_str(self) -> str:
        now_utc = datetime.now(timezone.utc)
        now_mad = now_utc.astimezone(ZoneInfo('Europe/Madrid'))
        return now_mad.strftime('%Y-%m-%d %H:%M:%S')

    def _log(self, msg: str, notify_telegram: bool = False):
        text = f'[{SYSTEM_NAME}] 🧱 [{self._now_str()}] {msg}'
        print(text)
        if notify_telegram:
            send_telegram_message(text)

    def fetch_latest_candle(self):
        backoff = 1
        while True:
            try:
                data = self.exchange.fetch_ohlcv(
                    self.symbol,
                    timeframe=self.timeframe,
                    limit=2
                )
                return data[-1]
            except Exception as e:
                self._log(f'⚠️ fetch_latest_candle error: {e}, retry in {backoff}s')
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def init_renko(self, close_price: float):
        boxes = round(close_price / self.ValueX)
        self.renkoMax = boxes * self.ValueX
        self.renkoMin = self.renkoMax - self.ValueX
        self._log(
            f'🧼 Init Renko → renkoMax={self.renkoMax:.2f}, renkoMin={self.renkoMin:.2f}, boxSize={self.ValueX:.2f}',
            notify_telegram=True
        )

    def update_renko(self, high: float, low: float):
        moved = False
        while high >= self.renkoMax + self.ValueX:
            self.renkoMax += self.ValueX
            self.renkoMin += self.ValueX
            moved = True
        while low <= self.renkoMin - self.ValueX:
            self.renkoMax -= self.ValueX
            self.renkoMin -= self.ValueX
            moved = True
        if moved:
            self._log(
                f'🧼 Renko updated → renkoMax={self.renkoMax:.2f}, renkoMin={self.renkoMin:.2f}, boxSize={self.ValueX:.2f}'
            )

    def check_signals(self, high: float, low: float) -> Optional[str]:
        if high >= self.renkoMax + self.ValueX:
            return 'long'
        if low <= self.renkoMin - self.ValueX:
            return 'short'
        return None

    def send_order(self, side: str):
        clientOrderId = f'{SYSTEM_NAME}-{int(time.time()*1000)}'
        params = {
            'clientOrderId': clientOrderId,
            'timeInForce': 'GTC',
            'reduceOnly': False,
            'postOnly': False,
            'leverage': 1,
        }
        order = self.exchange.create_order(
            self.symbol,
            'market',
            side,
            self.position_size,
            None,
            params
        )
        self._log(f'✅ Order {side.upper()} executed: {order["id"]}', notify_telegram=True)
        return order

    def run(self):
        self._log('🧬 Bot RENKO activated, waiting for signal...', notify_telegram=True)
        while True:
            ts, open_p, high, low, close, _ = self.fetch_latest_candle()
            if self.renkoMax is None:
                self.init_renko(close)
                time.sleep(60)
                continue
            self._log(
                f'🔄 Cycle → renkoMax={self.renkoMax:.2f}, renkoMin={self.renkoMin:.2f}, high={high:.2f}, low={low:.2f}'
            )
            self.update_renko(high, low)
            signal = self.check_signals(high, low)
            if signal == 'long' and self.long_count < self.max_orders:
                self.long_count += 1
                self.short_count = 0
                self._log(
                    f'🚀 LONG signal: high={high:.2f} ≥ {self.renkoMax + self.ValueX:.2f} | longs={self.long_count}/{self.max_orders}',
                    notify_telegram=True
                )
                self.send_order('buy')
            elif signal == 'short' and self.short_count < self.max_orders:
                self.short_count += 1
                self.long_count = 0
                self._log(
                    f'🚨 SHORT signal: low={low:.2f} ≤ {self.renkoMin - self.ValueX:.2f} | shorts={self.short_count}/{self.max_orders}',
                    notify_telegram=True
                )
                self.send_order('sell')
            else:
                self._log('🔕 No signal or limit reached')
            time.sleep(60)

if __name__ == '__main__':
    RenkoTrader().run()
