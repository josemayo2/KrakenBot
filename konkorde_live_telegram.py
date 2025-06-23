# konkorde_live_telegram.py
# Versión actualizada para reflejar al 100% la lógica del ProRealCode del sistema KONKORDE (Heiken Ashi 4h),
# enviando únicamente señales a Telegram (no abre órdenes reales en Kraken).

import ccxt
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta, timezone
import pytz
import requests

# =======================
# === CONFIGURACIÓN ====
# =======================
api_key = "TU_API_KEY"
api_secret = "TU_API_SECRET"

TELEGRAM_TOKEN   = "TU_TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID = "TU_CHAT_ID"

exchange = ccxt.krakenfutures({
    'apiKey': api_key,
    'secret': api_secret,
    'enableRateLimit': True,
})
exchange.load_markets()

symbol     = "BTC/USD:USD"
timeframe  = "4h"
order_size = 0.002  # (no se usa para enviar órdenes, solo referencia)

# Filtros de StopLoss / TakeProfit / Trailing (no se usan porque no abrimos posiciones reales)
stop_loss_pct    = 0.015
take_profit_pct  = 0.01
trailing_pct     = 0.005

# Zona horaria para mostrar logs / horas en Madrid
log_tz = pytz.timezone("Europe/Madrid")

# =======================
# === UTILIDADES  ======
# =======================
def log(msg):
    """Imprime un mensaje con timestamp en hora de Madrid."""
    ahora = datetime.now(log_tz).strftime('%Y-%m-%d %H:%M:%S')
    print(f"[KONKORDE] 🤬 [{ahora}] {msg}")

def enviar_telegram_konkorde(mensaje: str):
    """
    Envía un mensaje de texto SMS a Telegram.
    El formato que usamos es: [KONKORDE] 🔮 <mensaje>
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": f"[KONKORDE] 🔮 {mensaje}"
    }
    try:
        requests.post(url, data=payload)
    except Exception as e:
        log(f"Error al enviar mensaje a Telegram: {e}")

# =======================
# === VARIABLES GLOBALES PARA POSICIÓN =====
# =======================
# position = 0 => sin posición.  1 => estamos en largo.  -1 => estamos en corto.
position = 0

# =======================
# === FUNCIONES DE INDICADORES ====
# =======================
def calcular_heiken_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega columnas Heiken-Ashi a df: HAopen, HAhigh, HAlow, HAclose, isGreen, isRed
    Fórmulas EXACTAS del ProRealCode (sin recursividad).
    """
    df = df.copy()
    # HAopen = (open + close) / 2
    df['HAopen'] = (df['open'] + df['close']) / 2
    # HAclose = (open + high + low + close) / 4
    df['HAclose'] = (df[['open', 'high', 'low', 'close']].sum(axis=1)) / 4
    # HAhigh = max(high, HAopen, HAclose)
    df['HAhigh'] = df[['high', 'HAopen', 'HAclose']].max(axis=1)
    # HAlow = min(low, HAopen, HAclose)
    df['HAlow']  = df[['low', 'HAopen', 'HAclose']].min(axis=1)
    # isGreen / isRed
    df['isGreen'] = df['HAclose'] > df['HAopen']
    df['isRed']   = df['HAclose'] < df['HAopen']
    return df

def calcular_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calcula RSI con la librería 'ta'. Devuelve una serie con el RSI.
    """
    from ta.momentum import RSIIndicator
    rsi_series = RSIIndicator(close=df['close'], window=period).rsi()
    return rsi_series

def calcular_adx(df: pd.DataFrame, period: int = 12) -> pd.Series:
    """
    Calcula ADX con la librería 'ta'. Devuelve una serie con el ADX.
    """
    from ta.trend import ADXIndicator
    adx = ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=period).adx()
    return adx

def calcular_ema(df: pd.DataFrame, span: int) -> pd.Series:
    """
    Exponential Moving Average sobre precios de cierre.
    """
    return df['close'].ewm(span=span, adjust=False).mean()

def calcular_aroon(df: pd.DataFrame, period: int = 12) -> (pd.Series, pd.Series):
    """
    Calcula Aroon Up y Aroon Down usando la librería 'ta' para simplificar.
    Devuelve (aroon_up, aroon_down).
    """
    from ta.trend import AroonIndicator
    aroon_ind = AroonIndicator(high=df['high'], low=df['low'], window=period)
    return aroon_ind.aroon_up(), aroon_ind.aroon_down()

def calcular_obv(df: pd.DataFrame) -> pd.Series:
    """
    Calcula OBV de forma manual (sin ta). OBV[i] = OBV[i-1] + volumen si close>prev_close,
                   = OBV[i-1] - volumen si close<prev_close, sino igual.
    """
    obv = [0]
    for i in range(1, len(df)):
        if df['close'].iat[i] > df['close'].iat[i-1]:
            obv.append(obv[-1] + df['volume'].iat[i])
        elif df['close'].iat[i] < df['close'].iat[i-1]:
            obv.append(obv[-1] - df['volume'].iat[i])
        else:
            obv.append(obv[-1])
    return pd.Series(obv, index=df.index)

def aplicar_filtros_calendario(df: pd.DataFrame) -> (pd.Series, pd.Series):
    """
    Crea dos series booleanas: AllowLong y AllowShort según el ProRealCode.
    - NoDaysLong: día de semana = 2 ó 7  => sin largos (martes o domingo).
    - NoDaysShort: día de semana = 5 => sin cortos (viernes).
      (ProReal considera DayOfWeek: 1=lunes, 2=martes, ..., 7=domingo)
    - NoMonthsLong: mes = 10 => sin largos en octubre.
    - NoMonthsShort: mes = 7 => sin cortos en julio.
    - TradeHour: siempre True porque Time ∈ [000000,240000].
    """
    # Pandas dayofweek: 0=lunes, 1=martes, ..., 6=domingo
    # ProReal dayofweek = pandas_dayofweek + 1
    dow = df.index.dayofweek + 1  # 1..7
    month = df.index.month        # 1..12

    # NoDaysLong = (DayOfWeek == 2) or (DayOfWeek == 7)
    no_days_long  = (dow == 2) | (dow == 7)
    # NoDaysShort = (DayOfWeek == 5)
    no_days_short = (dow == 5)

    # NoMonthsLong = (Month == 10)
    no_months_long  = (month == 10)
    # NoMonthsShort = (Month == 7)
    no_months_short = (month == 7)

    # TradeHour = True para todo instante de la barra de 4h
    trade_hour = pd.Series(True, index=df.index)

    allow_long  = (~no_days_long)  & (~no_months_long)  & trade_hour
    allow_short = (~no_days_short) & (~no_months_short) & trade_hour

    return allow_long, allow_short

# =======================
# === FETCH OHLCV  ======
# =======================
def fetch_ohlcv() -> list:
    """
    Obtiene datos OHLCV de Kraken Futures para las últimas ~400 barras de 4h.
    Si hay error, reintenta hasta 3 veces (con sleep).
    """
    now_utc = datetime.now(timezone.utc)
    since   = exchange.parse8601((now_utc - timedelta(hours=400)).strftime('%Y-%m-%dT%H:%M:%SZ'))
    for _ in range(3):
        try:
            return exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        except Exception as e:
            log(f"⚠️ Error al obtener OHLCV: {e}. Reintentando en 10 s…")
            time.sleep(10)
    return None

# =======================
# === EJECUTAR CICLO ====
# =======================
def ejecutar_ciclo():
    global position

    log("🔄 Ejecutando ciclo 4h…")
    log("📈 Cargando datos OHLCV…")
    ohlcv = fetch_ohlcv()
    if ohlcv is None:
        log("❌ No se pudo obtener OHLCV. Abortando ciclo.")
        return

    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    if df.empty or len(df) < 20:
        log("❌ No hay suficientes datos OHLCV para calcular indicadores. Esperando próximo ciclo…")
        return

    # Convertir timestamp (ms) a datetime UTC y luego lo dejamos en UTC. Convertiremos a Madrid solo para mostrar.
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)

    # Calculamos los indicadores uno a uno
    log("🧮 Calculando indicadores técnicos…")
    # 1) Heiken Ashi
    df = calcular_heiken_ashi(df)

    # 2) RSI (14)
    df['RSI_14'] = calcular_rsi(df, period=14)

    # 3) ADX (12)
    df['ADX_12'] = calcular_adx(df, period=12)

    # 4) Cruce de EMAs: 12 vs 18 sobre precio de CIERRE
    df['EMA_12'] = calcular_ema(df, span=12)
    df['EMA_18'] = calcular_ema(df, span=18)
    df['CrossOver']  = df['EMA_12'] > df['EMA_18']
    df['CrossUnder'] = df['EMA_12'] < df['EMA_18']

    # 5) Aroon (12) - usamos ta para simplificar
    df['AroonUP'], df['AroonDOWN'] = calcular_aroon(df, period=12)
    df['AroonFilterLong']  = df['AroonUP']   > df['AroonDOWN']
    df['AroonFilterShort'] = df['AroonDOWN'] > df['AroonUP']

    # 6) OBV manual + filtros finales
    df['OBV'] = calcular_obv(df)
    # Filtro final: OBV > OBV[6] para largos, OBV < OBV[3] para cortos
    df['OBVFilterLong']  = df['OBV'] > df['OBV'].shift(6)
    df['OBVFilterShort'] = df['OBV'] < df['OBV'].shift(3)

    # 7) Filtros de calendario/hora
    allow_long, allow_short = aplicar_filtros_calendario(df)
    df['AllowLong']  = allow_long
    df['AllowShort'] = allow_short

    # 8) Filtros RSI simples
    df['RSIFilterLong']  = df['RSI_14'] > 10
    df['RSIFilterShort'] = df['RSI_14'] < 55

    # 9) Filtro ADX > 20
    df['ADXFilter'] = df['ADX_12'] > 20

    # Ahora determinamos la fila más reciente y evaluamos las condiciones:
    ultimo = df.iloc[-1]          # pandas Series de la última fila
    ts_utc = df.index[-1]         # Timestamp UTC
    ts_madrid = ts_utc.tz_convert(log_tz)
    precio   = float(ultimo['close'])

    # Condiciones de entrada Long
    longSignal = (
        (ultimo['isGreen'])           # vela HA verde
        and (ultimo['RSIFilterLong']) # RSI > 10
        and (ultimo['CrossOver'])     # EMA12 > EMA18
        and (ultimo['ADXFilter'])     # ADX > 20
        and (ultimo['AroonFilterLong'])
        and (ultimo['OBVFilterLong'])
        and (ultimo['AllowLong'])
        and (position == 0)
    )

    # Condiciones de entrada Short
    shortSignal = (
        (ultimo['isRed'])             # vela HA roja
        and (ultimo['RSIFilterShort'])# RSI < 55
        and (ultimo['CrossUnder'])    # EMA12 < EMA18
        and (ultimo['ADXFilter'])
        and (ultimo['AroonFilterShort'])
        and (ultimo['OBVFilterShort'])
        and (ultimo['AllowShort'])
        and (position == 0)
    )

    # Condiciones de salida de Long: si estamos en largo y aparece vela HA roja
    exitLongSignal = (
        (position == 1)
        and (ultimo['isRed'])
    )

    # Condiciones de salida de Short: si estamos en corto y aparece vela HA verde
    exitShortSignal = (
        (position == -1)
        and (ultimo['isGreen'])
    )

    # Ejecutamos las señales
    if longSignal:
        # Enviamos señal larga
        mensaje = (
            f"SEÑAL LARGO (4h)\n"
            f"Hora: {ts_madrid.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
            f"Precio: {precio:.2f}"
        )
        log(f"📈 {mensaje.replace(chr(10), ' | ')}")
        enviar_telegram_konkorde(mensaje)
        position = 1  # ahora estamos en largo

    elif shortSignal:
        # Enviamos señal corta
        mensaje = (
            f"SEÑAL CORTO (4h)\n"
            f"Hora: {ts_madrid.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
            f"Precio: {precio:.2f}"
        )
        log(f"📉 {mensaje.replace(chr(10), ' | ')}")
        enviar_telegram_konkorde(mensaje)
        position = -1  # ahora estamos en corto

    elif exitLongSignal:
        # Salimos de largo
        mensaje = (
            f"EXIT LARGO (4h)\n"
            f"Hora: {ts_madrid.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
            f"Precio: {precio:.2f}"
        )
        log(f"❌ {mensaje.replace(chr(10), ' | ')}")
        enviar_telegram_konkorde(mensaje)
        position = 0

    elif exitShortSignal:
        # Salimos de corto
        mensaje = (
            f"EXIT CORTO (4h)\n"
            f"Hora: {ts_madrid.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
            f"Precio: {precio:.2f}"
        )
        log(f"❌ {mensaje.replace(chr(10), ' | ')}")
        enviar_telegram_konkorde(mensaje)
        position = 0

    else:
        log("🔕 Ciclo finalizado sin señal.")

# =======================
# === FUNCIÓN PRINCIPAL ====
# =======================
def main():
    log("🧬 Activado correctamente. Esperando señal...")
    while True:
        try:
            ejecutar_ciclo()
        except Exception as e:
            log(f"❌ Error durante el ciclo: {e}")

        # Calculamos cuánto falta hasta el siguiente cierre de barra 4h.
        now = datetime.now(log_tz)
        # Las barras 4h cierran a horas: 00:00, 04:00, 08:00, 12:00, 16:00, 20:00
        # Podemos calcularlo así:
        minutos_actual = now.hour * 60 + now.minute
        # Próximo múltiplo de 240 minutos
        proximo_cierre_min = ( (minutos_actual // 240) + 1 ) * 240
        # Si estamos justo en un múltiplo, proximo_cierre = minutos_actual; 
        # Queremos que duerma hasta la próxima barra, así que si modulo es cero, sumamos 240
        if minutos_actual % 240 == 0:
            proximo_cierre_min = minutos_actual + 240
        minutos_restantes = proximo_cierre_min - minutos_actual
        segundos_espera = minutos_restantes * 60 - now.second - now.microsecond/1_000_000

        siguiente_cierre = now + timedelta(seconds=segundos_espera)
        log(f"⏰ Esperando {segundos_espera/3600:.2f} h hasta cierre 4h: {siguiente_cierre.strftime('%Y-%m-%dT%H:%M:%S')} {log_tz.zone}")
        time.sleep(max(segundos_espera, 0))

if __name__ == "__main__":
    main()

