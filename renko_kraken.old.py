import ccxt
import time
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo  # Para mostrar hora local (Europe/Madrid)
from typing import Optional  # <- Para Python 3.9, sustituye str | None

# === CONFIGURACIÓN TELEGRAM ===
TELEGRAM_TOKEN   = '8127892735:AAGZDT0xOzLpV01ISH5LKN3wG7INEL12Xrk'
TELEGRAM_CHAT_ID = '925121458'

def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {'chat_id': TELEGRAM_CHAT_ID, 'text': text}
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"[Telegram Error] {e}")


class RenkoTrader:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbol: str = 'BTC/USD:USD',
        timeframe: str = '1m',
        position_size: float = 0.04,
        max_orders: int = 5,                # <— parámetro configurable
    ):
        # Configuración de CCXT para Kraken Futures
        self.exchange = ccxt.krakenfutures({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
        })
        self.symbol = symbol
        self.timeframe = timeframe
        self.position_size = position_size

        # Parámetros RENKO (box size)
        self.StartingValue = 20       # Tamaño inicial del brick en USD
        self.ValueX = self.StartingValue
        # (En este ejemplo, ValueX permanece fijo en 20)

        # Variables internas RENKO
        self.renkoMax = None
        self.renkoMin = None

        # Contadores de operaciones consecutivas en cada sentido
        self.long_count = 0
        self.short_count = 0
        self.max_orders = max_orders   # <— guardamos el límite que viene por parámetro

    def _now_str(self) -> str:
        now_utc = datetime.now(timezone.utc)
        now_mad = now_utc.astimezone(ZoneInfo("Europe/Madrid"))
        return now_mad.strftime("%Y-%m-%d %H:%M:%S")

    def _log(self, msg: str, notify_telegram: bool = False):
        text = f"[RENKO] 🧱 [{self._now_str()}] {msg}"
        print(text)
        if notify_telegram:
            send_telegram_message(text)

    def fetch_latest_candle(self):
        ohlcv = self.exchange.fetch_ohlcv(self.symbol, timeframe=self.timeframe, limit=2)
        return ohlcv[-1]  # [timestamp, open, high, low, close, volume]

    def init_renko(self, close_price: float):
        boxes = round(close_price / self.ValueX)
        self.renkoMax = boxes * self.ValueX
        self.renkoMin = self.renkoMax - self.ValueX
        self._log(
            f"🧼 Inicializo Renko → renkoMax={self.renkoMax:.2f}, "
            f"renkoMin={self.renkoMin:.2f}, boxSize={self.ValueX:.2f}",
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
                f"🧼 Renko actualizado → renkoMax={self.renkoMax:.2f}, "
                f"renkoMin={self.renkoMin:.2f}, boxSize={self.ValueX:.2f}"
            )

    def check_signals(self, high: float, low: float) -> Optional[str]:
        """
        Devuelve 'long' si high ≥ renkoMax + boxSize,
        'short' si low ≤ renkoMin - boxSize,
        o None en caso contrario.
        """
        if high >= self.renkoMax + self.ValueX:
            return 'long'
        elif low <= self.renkoMin - self.ValueX:
            return 'short'
        return None

    def run(self):
        self._log("🧬 Bot RENKO activado. Esperando señal...", notify_telegram=True)

        while True:
            try:
                candle = self.fetch_latest_candle()
                ts_ms, open_p, high, low, close, _ = candle

                if self.renkoMax is None or self.renkoMin is None:
                    self.init_renko(close)
                    time.sleep(60)
                    continue

                self._log(
                    f"🔄 Ciclo 1m → renkoMax={self.renkoMax:.2f}, renkoMin={self.renkoMin:.2f}, "
                    f"high={high:.2f}, low={low:.2f}"
                )

                self.update_renko(high, low)
                signal = self.check_signals(high, low)

                if signal == 'long':
                    if self.long_count < self.max_orders:
                        self.long_count += 1
                        self.short_count = 0
                        self._log(
                            f"🚀 Señal LONG detectada: high={high:.2f} ≥ "
                            f"{self.renkoMax + self.ValueX:.2f} | "
                            f"Largos consec.: {self.long_count}/{self.max_orders}",
                            notify_telegram=True
                        )
                        # self.exchange.create_market_buy_order(self.symbol, self.position_size)
                    else:
                        self._log(
                            f"⚠️ Límite de LARGOS consecutivos ({self.max_orders}) alcanzado. "
                            "Ignorando señal LONG.",
                            notify_telegram=True
                        )

                elif signal == 'short':
                    if self.short_count < self.max_orders:
                        self.short_count += 1
                        self.long_count = 0
                        self._log(
                            f"🛑 Señal SHORT detectada: low={low:.2f} ≤ "
                            f"{self.renkoMin - self.ValueX:.2f} | "
                            f"Cortos consec.: {self.short_count}/{self.max_orders}",
                            notify_telegram=True
                        )
                        # self.exchange.create_market_sell_order(self.symbol, self.position_size)
                    else:
                        self._log(
                            f"⚠️ Límite de CORTOS consecutivos ({self.max_orders}) alcanzado. "
                            "Ignorando señal SHORT.",
                            notify_telegram=True
                        )

                else:
                    self._log("🔕 Ciclo finalizado sin señal.")

                time.sleep(60)

            except Exception as e:
                self._log(f"ERROR general: {str(e)}")
                time.sleep(60)


if __name__ == '__main__':
    API_KEY = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
    API_SECRET = 'gBvwaAYZE4GQdFvUB6VqjZhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'

    # Puedes cambiar max_orders al instanciar (por defecto 5)
    trader = RenkoTrader(API_KEY, API_SECRET, position_size=0.04, max_orders=5)
    trader.run()


