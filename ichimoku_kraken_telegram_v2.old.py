#!/usr/bin/env python3
import ccxt
import pandas as pd
import time
from datetime import datetime, timedelta, timezone
import pytz
import requests

# === CONFIGURACIÓN ===
API_KEY    = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'

exchange = ccxt.krakenfutures({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
})
exchange.load_markets()

SYMBOL           = "BTC/USD:USD"    # Símbolo para operar fraccionado sobre US Dollars
ORDER_AMOUNT_BTC = 0.05             # Tamaño de la posición (en BTC)
STOPP    = 0.015   # 1.5%
TAKEP    = 0.01    # 1.0%
TRAILP   = 0.005   # 0.5% ==> en Kraken Futs lo enviamos como “trailingPercent = TRAILP * 100”

tz = pytz.timezone("Europe/Madrid")

# Para controlar “un trade por día”
last_trade_date = None


# === UTILIDADES ===
def log(msg):
    """Log con sello horario en Europa/Madrid."""
    ahora = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
    print(f"[ICHIMOKU] 🌀 [{ahora}] {msg}")


def enviar_telegram_ichimoku(mensaje):
    """Envía notificación a tu bot de Telegram."""
    TOKEN   = '8127892735:AAGZDT0xOzLpV01ISH5LKN3wG7INEL12Xrk'
    CHAT_ID = '925121458'
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": f"[ICHIMOKU] 🌀 {mensaje}"}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"[ICHIMOKU] 🌀 Error al enviar mensaje Telegram: {e}")


def is_valid_time(now):
    """
    Reproduce:
      EntryTime = time>=070000 and time<=201000 
                  and not (time>=140000 and time<=170500)
    """
    t = int(now.strftime("%H%M%S"))
    # 07:00:00 – 20:10:00
    if not (70000 <= t <= 201000):
        log("❌ No entra: fuera del horario general (07:00–20:10).")
        return False
    # Bloqueo de 14:00:00 – 17:05:00
    if 140000 <= t <= 170500:
        log("❌ No entra: entre 14:00 y 17:05, bloqueado.")
        return False
    return True


def is_valid_day(now, is_long):
    """
    En ProRealCode:
      LONGENTRY bloquea si MONTH = 5
      SHORTENTRY no lo bloquea.
      Además bloquea si DAYOFWEEK = 5 (viernes).
    En Python, datetime.weekday(): lunes=0 ... domingo=6, así que viernes = 4.
    """
    # Viernes bloqueado para ambos (long y short)
    if now.weekday() == 4:
        log("❌ No entra: es viernes.")
        return False
    # Mes = 5 => bloquear para largos
    if is_long and now.month == 5:
        log("❌ No entra: mayo y largos bloqueados.")
        return False
    return True


def fetch_ohlcv(symbol, tf):
    """
    Trae OHLCV de Kraken Futures. Ante fallo, hace 3 reintentos.
    timeframe: '1m', '5m', '15m', etc.
    """
    now_utc = datetime.now(timezone.utc)
    # Pedimos un histórico suficientemente amplio (1000 velas)
    since = exchange.parse8601((now_utc - timedelta(minutes=1000)).strftime('%Y-%m-%dT%H:%M:%SZ'))
    for intento in range(3):
        try:
            data = exchange.fetch_ohlcv(symbol, tf, since=since, limit=1000)
            return data
        except Exception as e:
            log(f"⚠️ Error {tf} {intento+1}/3: {e}. Reintentando en 10 s…")
            time.sleep(10)
    log(f"❌ Error persistente al obtener velas de {symbol} en {tf}. Abortando este ciclo.")
    return None


def compute_ichimoku_bandas(df):
    """
    Calcula Tenkan (9), Kijun (26), SenkouSpanA (shift 26), SenkouSpanB (shift 26 + rolling 52).
    Retorna un DataFrame con columnas: tenkan, kijun, spanA, spanB, chikou.
    - spanA = ((tenkan.shift(26) + kijun.shift(26)) / 2)
    - spanB = ((high.shift(26).rolling(52).max() + low.shift(26).rolling(52).min()) / 2)
    - chikou = close (en ProReal se define Chikou1 = close, y luego usan spanA[26], spanB[26]).
    """
    df2 = df.copy()
    df2['high_shift26'] = df2['high'].shift(26)
    df2['low_shift26']  = df2['low'].shift(26)

    # Tenkan (9) y Kijun (26)
    df2['tenkan'] = (df2['high'].rolling(9).max() + df2['low'].rolling(9).min()) / 2
    df2['kijun']  = (df2['high'].rolling(26).max() + df2['low'].rolling(26).min()) / 2

    # Senkou Span A (se desplaza 26 adelante, pero aquí lo guardamos sin desplazar; usaremos shift(26) arriba)
    #    spanA_t = (tenkan_{t-26} + kijun_{t-26}) / 2  => equivalente a ((tenkan.shift(26) + kijun.shift(26))/2)
    df2['spanA'] = (df2['tenkan'].shift(26) + df2['kijun'].shift(26)) / 2

    # Senkou Span B: rolling(52) sobre high_shift26 / low_shift26
    df2['spanB'] = ( df2['high_shift26'].rolling(52).max() + df2['low_shift26'].rolling(52).min() ) / 2

    # Chikou: en ProRealCode definen Chikou = close (sin shift)
    df2['chikou'] = df2['close']

    # Limpiamos columnas auxiliares
    df2.drop(columns=['high_shift26','low_shift26'], inplace=True)
    return df2


def confirm_multiframe():
    """
    Para cada timeframe (1m, 5m, 15m):
      - Descarga ohlcv y arma DataFrame.
      - Calcula Ichimoku (tenkan, kijun, spanA, spanB, chikou).
      - En cada timeframe:
          > ca1 = condición de Kumo para compra
          > cv1 = condición de Kumo para venta
          > ca2 = condición de Chikou para compra
          > cv2 = condición de Chikou para venta
          > (en 1m, sumamos al ca2 la lógica de cruce de kijun & tenkan)
      - Devuelve (buy_signal, sell_signal, precio_último 1m).
    """
    try:
        datos_1m  = fetch_ohlcv(SYMBOL, '1m')
        datos_5m  = fetch_ohlcv(SYMBOL, '5m')
        datos_15m = fetch_ohlcv(SYMBOL, '15m')
        if datos_1m is None or datos_5m is None or datos_15m is None:
            return False, False, None

        # Armar DataFrames
        df1 = pd.DataFrame(datos_1m, columns=['ts','open','high','low','close','volume'])
        df5 = pd.DataFrame(datos_5m, columns=['ts','open','high','low','close','volume'])
        df15 = pd.DataFrame(datos_15m, columns=['ts','open','high','low','close','volume'])

        for df in (df1, df5, df15):
            df['ts'] = pd.to_datetime(df['ts'], unit='ms')
            df.set_index('ts', inplace=True)

        # Calcular Ichimoku en cada timeframe
        ich1 = compute_ichimoku_bandas(df1)
        ich5 = compute_ichimoku_bandas(df5)
        ich15 = compute_ichimoku_bandas(df15)

        # Extraer la última fila de cada DataFrame
        last1  = ich1.iloc[-1]
        last5  = ich5.iloc[-1]
        last15 = ich15.iloc[-1]

        # ============================
        # –––––––  TIMEFRAME 15m  ––––––
        # ============================
        # Kumo compra:  (close > spanA and spanA > spanB)  OR  (close > spanB and spanB > spanA)
        ca1m60 = ((last15['close'] > last15['spanA'] and last15['spanA'] > last15['spanB']) or
                  (last15['close'] > last15['spanB'] and last15['spanB'] > last15['spanA']))
        # Kumo venta:
        cv1m60 = ((last15['close'] < last15['spanA'] and last15['spanA'] < last15['spanB']) or
                  (last15['close'] < last15['spanB'] and last15['spanB'] < last15['spanA']))
        # Chikou compra: close_t > spanA_{t-26} AND close_t > spanB_{t-26}
        #   spanA.shift(26) existe 26 filas atrás; comprobamos que no sea NaN.
        if len(ich15) >= 27:
            spanA_15_t26 = ich15['spanA'].iloc[-27]  # 26 barras atrás
            spanB_15_t26 = ich15['spanB'].iloc[-27]
            ca2m60 = (last15['close'] > spanA_15_t26) and (last15['close'] > spanB_15_t26)
            cv2m60 = (last15['close'] < spanA_15_t26) and (last15['close'] < spanB_15_t26)
        else:
            ca2m60 = False
            cv2m60 = False

        # ============================
        # –––––––  TIMEFRAME 5m  ––––––
        # ============================
        last5_idx = ich5.iloc[-1]
        # Kumo compra:
        ca1m15 = ((last5_idx['close'] > last5_idx['spanA'] and last5_idx['spanA'] > last5_idx['spanB']) or
                  (last5_idx['close'] > last5_idx['spanB'] and last5_idx['spanB'] > last5_idx['spanA']))
        # Kumo venta:
        cv1m15 = ((last5_idx['close'] < last5_idx['spanA'] and last5_idx['spanA'] < last5_idx['spanB']) or
                  (last5_idx['close'] < last5_idx['spanB'] and last5_idx['spanB'] < last5_idx['spanA']))
        # Chikou 5m:
        if len(ich5) >= 27:
            spanA_5_t26 = ich5['spanA'].iloc[-27]
            spanB_5_t26 = ich5['spanB'].iloc[-27]
            ca2m15 = (last5_idx['close'] > spanA_5_t26) and (last5_idx['close'] > spanB_5_t26)
            cv2m15 = (last5_idx['close'] < spanA_5_t26) and (last5_idx['close'] < spanB_5_t26)
        else:
            ca2m15 = False
            cv2m15 = False

        # ============================
        # –––––––  TIMEFRAME 1m  ––––––
        # ============================
        last1_idx = ich1.iloc[-1]
        prev1_idx = ich1.iloc[-2]  # barra anterior
        # Kumo compra:
        ca1m5 = ((last1_idx['close'] > last1_idx['spanA'] and last1_idx['spanA'] > last1_idx['spanB']) or
                 (last1_idx['close'] > last1_idx['spanB'] and last1_idx['spanB'] > last1_idx['spanA']))
        # Kumo venta:
        cv1m5 = ((last1_idx['close'] < last1_idx['spanA'] and last1_idx['spanA'] < last1_idx['spanB']) or
                 (last1_idx['close'] < last1_idx['spanB'] and last1_idx['spanB'] < last1_idx['spanA']))

        # Chikou 1m (condición extendida):
        #   -> close_t > spanA_t AND close_t > spanB_t
        #   -> Y close ha cruzado sobre kijun: prev_close <= prev_kijun y close > kijun
        #   -> Y close > tenkan
        cross_over_kijun = (prev1_idx['close'] <= prev1_idx['kijun']) and (last1_idx['close'] > last1_idx['kijun'])
        cross_under_kijun= (prev1_idx['close'] >= prev1_idx['kijun']) and (last1_idx['close'] < last1_idx['kijun'])

        ca2m5 = (last1_idx['close'] > last1_idx['spanA']) and \
                (last1_idx['close'] > last1_idx['spanB']) and \
                cross_over_kijun and \
                (last1_idx['close'] > last1_idx['tenkan'])

        cv2m5 = (last1_idx['close'] < last1_idx['spanA']) and \
                (last1_idx['close'] < last1_idx['spanB']) and \
                cross_under_kijun and \
                (last1_idx['close'] < last1_idx['tenkan'])

        # ============================
        # ––– COMBINAR PARA SEÑALES ––
        # ============================
        buy_signal  = ca1m60 and ca2m60 and ca1m15 and ca2m15 and ca1m5  and ca2m5
        sell_signal = cv1m60 and cv2m60 and cv1m15 and cv2m15 and cv1m5  and cv2m5

        # Precio de cierre 1m para calcular SL/TP
        precio_ultimo = last1_idx['close']

        return buy_signal, sell_signal, precio_ultimo

    except Exception as e:
        log(f"❌ Error general en confirmación multiframe: {e}")
        return False, False, None


def enviar_orden(tipo, precio_entrada):
    """
    Envía orden MARKET a KrakenFutures + SL + TP + TrailingStop.
    Parámetros:
      tipo = 'buy'  (long)  o 'sell'  (short).
      precio_entrada = float (último close de 1m).
    """
    try:
        # Dump del tipo de orden y cantidad
        log(f"🚀 Enviando orden {tipo.upper()} de {ORDER_AMOUNT_BTC} BTC en {SYMBOL} 📈")
        # Calculamos SL/TP/Trailing
        sl    = precio_entrada * (1 - STOPP) if tipo == 'buy' else precio_entrada * (1 + STOPP)
        tp    = precio_entrada * (1 + TAKEP) if tipo == 'buy' else precio_entrada * (1 - TAKEP)
        trail = TRAILP * 100  # en KrakenFutures, es porcentaje (ej. 0.5% -> 0.5)

        # Preparamos params para KrakenFutures:
        params = {
            'stopLossPrice':    round(sl, 2),    # en USD
            'takeProfitPrice':  round(tp, 2),    # en USD
            'trailingPercent':  trail,           # en KrakenFutures va en % (ej. 0.5 => 0.5%)
        }
        # Creamos la orden de mercado con SL/TP/Trailing adjuntos
        order = exchange.create_order(
            symbol = SYMBOL,
            type   = 'market',
            side   = tipo,               # 'buy' o 'sell'
            amount = ORDER_AMOUNT_BTC,
            price  = None,
            params = params
        )
        log(f"✅ Orden ejecutada con ID {order['id']}")

        mensaje = f"{'✅ ORDEN LONG' if tipo=='buy' else '🚨 ORDEN SHORT'} ejecutada en BTC/USD a {precio_entrada:.2f} \n" + \
                  f"   • SL = {sl:.2f}   • TP = {tp:.2f}   • Trailing = {trail:.2f}%"
        enviar_telegram_ichimoku(mensaje)

    except Exception as e:
        log(f"❌ Error al enviar orden {tipo.upper()}: {e}")


def main():
    global last_trade_date

    enviar_telegram_ichimoku("🟢 Sistema Ichimoku activado. Esperando señales multiframe…")

    while True:
        now = datetime.now(tz)
        # Si cambió la fecha (midnight Madrid), reseteo newday
        if last_trade_date is None or now.date() != last_trade_date:
            last_trade_date = now.date()
            log("🔄 Nuevo día: permitiendo 1 trade de Ichimoku hoy.")

        try:
            buy, sell, precio = confirm_multiframe()
            if precio is None:
                log("❌ No entra: fallo en datos multiframe o conexión.")
                time.sleep(60)
                continue

            # ============================
            # –––––––– SEÑAL LONG ––––––––
            # ============================
            if buy:
                # Aún no ha hecho trade hoy (last_trade_date == hoy), y horario/día/mes ok
                if is_valid_time(now) and is_valid_day(now, True) and last_trade_date == now.date():
                    # Envío Long
                    enviar_orden("buy", precio)
                    # Marco que ya hice trade hoy
                    last_trade_date = now.date() + timedelta(days=1)  # Así no podrá volver a entrar hasta que cambie la fecha
                else:
                    log("❌ No cumple filtros para LONG o ya se ha tradeado hoy.")

            # ============================
            # –––––––– SEÑAL SHORT –––––––
            # ============================
            elif sell:
                if is_valid_time(now) and is_valid_day(now, False) and last_trade_date == now.date():
                    # Envío Short
                    enviar_orden("sell", precio)
                    # Marco trade del día
                    last_trade_date = now.date() + timedelta(days=1)
                else:
                    log("❌ No cumple filtros para SHORT o ya se ha tradeado hoy.")

            else:
                log("⏳ No hay señal técnica confirmada en los 3 marcos.")

        except Exception as e:
            log(f"❌ Error general en main loop: {e}")

        # Espero 60 s antes de volver a chequear
        time.sleep(60)


if __name__ == "__main__":
    main()

