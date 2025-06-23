#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
renko2_kraken_maker.py

Versión actualizada con órdenes LIMIT tipo Maker.
Incluye lógica de cancelación si no se ejecuta tras 15 segundos.
"""

import ccxt
import time
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional, List

# === CONFIGURACIÓN BÁSICA ===
API_KEY        = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET     = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'
SYSTEM_NAME    = 'renko2_kraken_maker'
SYMBOL         = 'BTC/USD:USD'
TIMEFRAME      = '1m'
POSITION_SIZE  = 0.06
MAX_ORDERS     = 5

# === OPTIMIZACIÓN DINÁMICA DE Renko BOX SIZE ===
StartingValue = 20
ResetPeriod   = 1
Increment     = 10
MaxIncrement  = 7
Reps          = 3
MaxValue      = 150
MinValue      = Increment

# === Gestión de riesgos ===
STOP_LOSS_PCT      = 0.01
TARGET_PROFIT_PCT  = 0.011
TRAILING_START_PCT = 0.0049
TRAILING_STEP_PCT  = 0.003

# === Telegram ===
TELEGRAM_TOKEN   = '8127892735:AAGZDT0xOzLpV01ISH5LKN3wG7INEL12Xrk'
TELEGRAM_CHAT_ID = '925121458'

print(f"[{SYSTEM_NAME}] 🧱 Arrancando renko2_kraken_maker...")

class RenkoTrader:
    def __init__(self):
        self.exchange = ccxt.krakenfutures({
            'apiKey': API_KEY,
            'secret': API_SECRET,
            'enableRateLimit': True
        })
        self.box = StartingValue
        self.renkoMax = None
        self.renkoMin = None
        self.long_count = 0
        self.short_count = 0
        self.current_order = None
        self.pending_entry = None
        self.entry_price = None
        self.stop_price = None
        self.optimize_counter = 0
        self.last_perfs: List[float] = []
        self.PIncPos = 1
        self.NIncPos = 1
        self.Mode1 = 1
        self.WinCountB = 0
        self.StratAvgB = 0.0
        self.BestA = StartingValue
        self.BestB = StartingValue
        now = datetime.now(ZoneInfo("Europe/Madrid"))
        self.monthinit = now.month
        self.yearinit = now.year

    def _now_str(self) -> str:
        now = datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Madrid"))
        return now.strftime("%Y-%m-%d %H:%M:%S")

    def _log(self, msg: str, notify: bool = False):
        text = f"[{SYSTEM_NAME}] 🧱 [{self._now_str()}] {msg}"
        print(text)
        if notify:
            try:
                resp = requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    data={'chat_id': TELEGRAM_CHAT_ID, 'text': text},
                    timeout=10
                )
                if resp.status_code != 200:
                    print(f"[Telegram] ❌ Error {resp.status_code}: {resp.text}")
            except Exception as e:
                print(f"[Telegram] ⚠️ Exception sending message: {e}")


    def fetch_candle(self):
        backoff = 1
        while True:
            try:
                ohlcv = self.exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=2)
                return ohlcv[-1]
            except Exception as e:
                self._log(f"⚠️ fetch_candle error: {e}, retry in {backoff}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def init_renko(self, price: float):
        boxes = round(price / self.box)
        self.renkoMax = boxes * self.box
        self.renkoMin = self.renkoMax - self.box
        self._log(f"Init Renko → box={self.box:.2f}, Max={self.renkoMax:.2f}, Min={self.renkoMin:.2f}", notify=True)

    def update_renko(self, high: float, low: float):
        moved = False
        while high >= self.renkoMax + self.box:
            self.renkoMax += self.box
            self.renkoMin += self.box
            moved = True
        while low <= self.renkoMin - self.box:
            self.renkoMax -= self.box
            self.renkoMin -= self.box
            moved = True
        if moved:
            self._log(f"🔄 Renko updated → box={self.box:.2f}, Max={self.renkoMax:.2f}, Min={self.renkoMin:.2f}")

    def check_signal(self, high: float, low: float) -> Optional[str]:
        if high >= self.renkoMax + self.box:
            return 'long'
        if low <= self.renkoMin - self.box:
            return 'short'
        return None

    def execute_limit_entry(self, side: str, wait_seconds: int = 15):
        self._log(f"🚨 Señal detectada: entrada {side.upper()} (modo LIMIT)", notify=True)
        try:
            orderbook = self.exchange.fetch_order_book(SYMBOL)
            if side == 'buy':
                best_price = orderbook['bids'][0][0] if orderbook['bids'] else None
                limit_price = best_price - 5 if best_price else None
            else:
                best_price = orderbook['asks'][0][0] if orderbook['asks'] else None
                limit_price = best_price + 5 if best_price else None

            if limit_price is None:
                self._log(f"❌ No se pudo determinar el precio límite para {side}", notify=True)
                return

            params = {
                'clientOrderId': f"{SYSTEM_NAME}-{int(time.time() * 1000)}",
                'timeInForce': 'GTC',
                'reduceOnly': False,
                'postOnly': True,
                'leverage': 1
            }

            order = self.exchange.create_limit_order(SYMBOL, side, POSITION_SIZE, limit_price, params)
            self.pending_entry = order
            self._log(f"✅ Orden LIMIT {side.upper()} colocada @ {limit_price:.2f}", notify=True)

            time.sleep(wait_seconds)

            # No fetch_order disponible, asumimos no ejecutada
            self.exchange.cancel_order(order['id'])
            self.pending_entry = None
            self._log(f"❌ Orden LIMIT {side.upper()} cancelada tras {wait_seconds}s sin ejecución", notify=True)

        except Exception as e:
            self._log(f"⚠️ Error en execute_limit_entry: {e}", notify=True)

    def update_trailing(self, last_price: float):
        if not self.current_order:
            return
        side = self.current_order['side']
        profit = (last_price - self.entry_price) if side == 'buy' else (self.entry_price - last_price)
        if profit <= 0:
            return
        start_pts = self.entry_price * TRAILING_START_PCT
        if profit < start_pts:
            return
        step_pts = self.entry_price * TRAILING_STEP_PCT
        candidate = (last_price - step_pts) if side == 'buy' else (last_price + step_pts)
        if (side == 'buy' and candidate > self.stop_price) or (side == 'sell' and candidate < self.stop_price):
            self.stop_price = candidate
            self._log(f"🔄 Trailing updated @ {self.stop_price:.2f}")

    def check_exit(self, last_price: float) -> bool:
        if not self.current_order:
            return False
        side = self.current_order['side']
        if side == 'buy' and last_price >= self.entry_price * (1 + TARGET_PROFIT_PCT):
            self._log(f"✅ TP @ {last_price:.2f}", notify=True)
            return True
        if side == 'sell' and last_price <= self.entry_price * (1 - TARGET_PROFIT_PCT):
            self._log(f"✅ TP @ {last_price:.2f}", notify=True)
            return True
        if side == 'buy' and last_price <= self.stop_price:
            self._log(f"🛑 Stop @ {self.stop_price:.2f}", notify=True)
            return True
        if side == 'sell' and last_price >= self.stop_price:
            self._log(f"🛑 Stop @ {self.stop_price:.2f}", notify=True)
            return True
        return False

    def close_position(self):
        exit_price = float(self.exchange.fetch_ticker(SYMBOL)['last'])
        perf = (exit_price - self.entry_price) if self.current_order['side'] == 'buy' else (self.entry_price - exit_price)
        self.last_perfs.append(perf)
        self.optimize_counter += 1

        side = 'sell' if self.current_order['side'] == 'buy' else 'buy'
        params = {'reduceOnly': True, 'timeInForce': 'GTC'}
        self.exchange.create_order(SYMBOL, 'market', side, POSITION_SIZE, None, params)

        self._log(f"🛑 Closed @ {exit_price:.2f} P/L={perf:.4f}", notify=True)

        if side == 'sell':
            self.short_count += 1
            self.long_count = 0
        else:
            self.long_count += 1
            self.short_count = 0

        self.current_order = None
        self.entry_price = None
        self.stop_price = None

        self.run_optimization()

    def run_optimization(self):
        now = datetime.now(ZoneInfo("Europe/Madrid"))
        months_passed = (now.year - self.yearinit) * 12 + (now.month - self.monthinit)
        if months_passed >= ResetPeriod:
            self.box = StartingValue
            self.last_perfs.clear()
            self.optimize_counter = 0
            self.monthinit, self.yearinit = now.month, now.year
            self.PIncPos = self.NIncPos = 1
            self._log("🔄 Monthly reset", notify=True)
            return
        if self.optimize_counter < Reps:
            return
        wins = sum(1 for p in self.last_perfs[-Reps:] if p > 0)
        avg = sum(self.last_perfs[-Reps:]) / Reps
        if avg >= self.StratAvgB:
            self.StratAvgB = avg
            self.BestA = self.box
        if wins >= self.WinCountB:
            self.WinCountB = wins
            self.BestB = self.box
        if wins < self.WinCountB and avg < self.StratAvgB and self.Mode1 == 1:
            self.box -= Increment * self.NIncPos
            self.NIncPos += 1
            self.Mode1 = 2
        else:
            self.box += Increment * self.PIncPos
            self.PIncPos += 1
            self.Mode1 = 1
        self.box = max(min(self.box, MaxValue), MinValue)
        self.optimize_counter = 0
        self._log(f"🔄 Optimized boxSize -> {self.box:.2f}", notify=True)

    def run(self):
        self._log("🧬 Bot RENKO2 activado", notify=True)
        while True:
            try:
                ts, o, h, l, c, v = self.fetch_candle()
                if self.renkoMax is None:
                    self.init_renko(c)
                    time.sleep(60)
                    continue

                self._log(f"🔄 Cycle: box={self.box:.2f}, Max={self.renkoMax:.2f}, Min={self.renkoMin:.2f}, H={h:.2f}, L={l:.2f}")
                self.update_renko(h, l)
                if self.check_exit(c):
                    self.close_position()
                else:
                    self.update_trailing(c)
                if not self.current_order and not self.pending_entry:
                    sig = self.check_signal(h, l)
                    if sig == 'long' and self.long_count < MAX_ORDERS:
                        self.execute_limit_entry('buy')
                    elif sig == 'short' and self.short_count < MAX_ORDERS:
                        self.execute_limit_entry('sell')
                time.sleep(60)
            except Exception as e:
                self._log(f"⚠️ runtime error: {e}", notify=True)
                time.sleep(60)

if __name__ == '__main__':
    RenkoTrader().run()
