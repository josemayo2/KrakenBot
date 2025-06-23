# mix_kraken.py
# -*- coding: utf-8 -*-
import sys

# Forzar stdout/stderr a UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import ccxt
import pandas as pd
import numpy as np
import time
import requests
import schedule
import html

from ta.volatility import AverageTrueRange, BollingerBands
from ta.trend import ADXIndicator
from ta.momentum import ROCIndicator

# ============================
# 1. CREDENCIALES KRAKEN FUTURES
# ============================
API_KEY    = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'

# ============================
# 2. TELEGRAM - CONFIGURACIÓN
# ============================
TELEGRAM_BOT_TOKEN = '8127892735:AAGZDT0xOzLpV01ISH5LKN3wG7INEL12Xrk'
TELEGRAM_CHAT_ID   = '925121458'

def telegram_send_message(text: str):
    """
    Envía un mensaje de texto (HTML) a Telegram usando BOT_TOKEN y CHAT_ID.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': 'HTML'
    }
    try:
        r = requests.post(url, data=payload, timeout=10)
        if not r.ok:
            print(f"[Telegram Error] 🔴 Código {r.status_code}: {r.text}")
    except Exception as e:
        print(f"[Telegram Exception] 🔴 {e}")

# ============================
# 3. PARÁMETROS GLOBALES
# ============================
SYMBOL                 = "BTC/USD:USD"   # Símbolo para operar en Kraken Futures
CAPITAL_USD            = 10000           # Solo para logs, no lo usamos en sizing

LONG_STOP_PERCENT      = 1.5     # % de stop-loss para largos
LONG_PROFIT_PERCENT    = 2.5     # % de take-profit para largos (después de la primera barra)
INITIAL_PROFIT_PERCENT = 0.31    # % de take-profit en la primera barra tras la entrada
L_TRAILING_PERCENT     = 0.25    # % para trailing dinámico en largos
N_BARS                 = 1000    # nbars (cada barra = 1 min) tras entrada para ajustar stop/TP

CONDICION1             = 1.03    # Lógica “A” de entrada
N_ENTRY_LIMIT          = 1       # 1 → orden LIMIT/CLOSE, 0 → MARKET instantáneo
MARKET_ENTRY           = True    # En ProReal, MARKETENTRY=1

# Tamaño fijo de posición (en contratos)
FIXED_CONTRACTS = 0.035

# ============================
# 4. CONEXIÓN A KRAKEN FUTURES (CCXT)
# ============================
exchange = ccxt.krakenfutures({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
})

# ============================
# 5. FUNCIONES AUXILIARES
# ============================
def in_entry_hours(timestamp: pd.Timestamp) -> bool:
    """
    ENTRYHOURS = HOUR>=0 AND HOUR<=19 
                  AND NOT (HOUR>=2 AND HOUR<=4) 
                  AND NOT (HOUR>=12 AND HOUR<=17).
    Se evalúa en zona "Europe/Madrid".
    """
    hora = timestamp.hour
    if not (0 <= hora <= 19):
        return False
    if 2 <= hora <= 4:
        return False
    if 12 <= hora <= 17:
        return False
    return True

def fetch_ohlcv_1m(symbol: str, limit: int = 2000) -> pd.DataFrame:
    """
    Descarga OHLCV de 1 minuto (timeframe='1m') de Kraken Futures,
    con hasta 3 reintentos si hay timeout (504).
    Devuelve DataFrame con índice UTC y columnas ['open','high','low','close','volume'].
    """
    attempts = 0
    while attempts < 3:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe="1m", limit=limit)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df.set_index("timestamp", inplace=True)
            return df
        except Exception as e:
            attempts += 1
            if attempts < 3:
                print(f"[fetch_ohlcv_1m] ⚠️ Timeout/API error, reintentando {attempts}/3...")
                time.sleep(5)
            else:
                raise

def compute_crisis(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula CRISIS = ATR(6)[daily] > ATR(47)[daily] shifted 31 * 1.53,
    replicando Timeframe(Daily, UpdateOnClose). Luego mapea el flag diario a cada vela 1m.
    Si no hay suficientes días (menos de 78 días), se asume False.
    """
    df_daily = df[["high", "low", "close"]].resample("D").agg({
        "high": "max",
        "low":  "min",
        "close":"last"
    }).dropna()

    # Si no hay al menos 78 días, no calculamos ATR → crisis=False
    if df_daily.shape[0] < 78:
        df["crisis"] = False
        return df

    atr6_daily  = AverageTrueRange(high=df_daily["high"], low=df_daily["low"], close=df_daily["close"],
                                   window=6, fillna=True).average_true_range()
    atr47_daily = AverageTrueRange(high=df_daily["high"], low=df_daily["low"], close=df_daily["close"],
                                   window=47, fillna=True).average_true_range()
    atr47_lag31 = atr47_daily.shift(31)

    crisis_daily = atr6_daily > (atr47_lag31 * 1.53)
    crisis_daily.index = crisis_daily.index.normalize()

    df["date_only"] = df.index.tz_convert("UTC").normalize()
    df["crisis"] = df["date_only"].map(crisis_daily).fillna(False)
    df.drop(columns=["date_only"], inplace=True)
    return df

def compute_keltner(df: pd.DataFrame) -> pd.DataFrame:
    """
    MiddleLine = SMA(10)
    ATRValue   = ATR(35)
    UpperKeltner = SMA(10) + 0.06 * ATR(35)
    LowerKeltner = SMA(10) - 0.03 * ATR(35)
    """
    df["ma10"]   = df["close"].rolling(window=10, min_periods=1).mean()
    df["atr_35"] = AverageTrueRange(high=df["high"], low=df["low"], close=df["close"],
                                     window=35, fillna=True).average_true_range()
    df["upper_keltner"] = df["ma10"] + 0.06 * df["atr_35"]
    df["lower_keltner"] = df["ma10"] - 0.03 * df["atr_35"]
    return df

def compute_adx(df: pd.DataFrame) -> pd.DataFrame:
    """
    ADX(5) sobre datos de 1 minuto.
    """
    df["adx_5"] = ADXIndicator(high=df["high"], low=df["low"], close=df["close"],
                               window=5, fillna=True).adx()
    return df

def compute_roc(df: pd.DataFrame) -> pd.DataFrame:
    """
    ROC(2) = (Close[t] - Close[t-2]) / Close[t-2] * 100 
    (ventana = 2 barras de 1 min).
    """
    df["roc_2"] = ROCIndicator(close=df["close"], window=2, fillna=True).roc()
    return df

def compute_bollinger_width(df: pd.DataFrame) -> pd.DataFrame:
    """
    Bollinger Band Width (BBWidth) para window=12 y window=22 (cada barra 1 min):
      width = (UpperBB - LowerBB) / MiddleBB
    LIVE  = width_12 > 5/875
    DEATH = width_22 > 5/1000
    """
    bb12   = BollingerBands(close=df["close"], window=12, window_dev=2, fillna=True)
    mavg12 = bb12.bollinger_mavg()
    hband12 = bb12.bollinger_hband()
    lband12 = bb12.bollinger_lband()
    width12 = (hband12 - lband12) / mavg12.replace(0, np.nan)
    df["bband_width_12"] = width12

    bb22   = BollingerBands(close=df["close"], window=22, window_dev=2, fillna=True)
    mavg22 = bb22.bollinger_mavg()
    hband22 = bb22.bollinger_hband()
    lband22 = bb22.bollinger_lband()
    width22 = (hband22 - lband22) / mavg22.replace(0, np.nan)
    df["bband_width_22"] = width22

    df["Live"]  = df["bband_width_12"] > (5 / 875)
    df["Death"] = df["bband_width_22"] > (5 / 1000)
    return df

def compute_test_tost(df: pd.DataFrame) -> pd.DataFrame:
    """
    TEST  = close < SMA(54) AND close < SMA(750) AND close < SMA(4975)
    TOST  = close > SMA(22) AND close > SMA(1150)
    """
    df["sma_54"]   = df["close"].rolling(window=54,   min_periods=1).mean()
    df["sma_750"]  = df["close"].rolling(window=750,  min_periods=1).mean()
    df["sma_4975"] = df["close"].rolling(window=4975, min_periods=1).mean()
    df["TEST"]     = (
        (df["close"] < df["sma_54"]) &
        (df["close"] < df["sma_750"]) &
        (df["close"] < df["sma_4975"])
    )

    df["sma_22"]   = df["close"].rolling(window=22,   min_periods=1).mean()
    df["sma_1150"] = df["close"].rolling(window=1150, min_periods=1).mean()
    df["TOST"]     = (df["close"] > df["sma_22"]) & (df["close"] > df["sma_1150"])
    return df

# ============================
# 6. ESTRATEGIA CADA MINUTO (1 MIN) Y ESTADO GLOBAL
# ============================
long_on_market = False
entry_index    = None  # Timestamp de entrada
entry_price    = None  # Precio de entrada
trade_size     = None  # Siempre = FIXED_CONTRACTS

def strategy_1m_loop(symbol: str, capital_initial: float):
    """
    Se ejecuta cada minuto:
    1) Descarga velas 1 minuto
    2) Verifica que haya al menos 60 filas, sino sale
    3) Calcula indicadores (con try/except para no romper si hay poca data)
    4) Monta condiciones de entrada/salida replicando ProRealCode
    5) Envía órdenes y notifica a Telegram
    """
    global long_on_market, entry_index, entry_price, trade_size

    # 1) Hora local en Madrid
    now_local = pd.Timestamp.now(tz="Europe/Madrid")
    print(f"[{now_local}] 🏗️ Ejecutando ciclo 1m…")

    # 2) Descargar velas 1m
    print(f"[{now_local}] 📊 Cargando datos OHLCV…")
    df = fetch_ohlcv_1m(symbol, limit=2000)

    # 3) Convertir índice a Europe/Madrid
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert("Europe/Madrid")

    # 4) ¿Tenemos suficientes filas para calcular indicadores?
    if df.shape[0] < 60:
        print(f"[{df.index[-1]}] ⚠️ Datos insuficientes ({df.shape[0]} barras). Saltando este ciclo.")
        return

    # 5) Si fuera de ENTRYHOURS, no hacemos nada
    ahora_min = df.index[-1]
    if not in_entry_hours(ahora_min):
        print(f"[{ahora_min}] 🛑 Fuera de ENTRYHOURS. Sin señal.")
        return

    # 6) Calcular indicadores: atrapamos cualquier excepción para no romper el loop
    try:
        df = compute_crisis(df)
        df = compute_keltner(df)
        df = compute_adx(df)
        df = compute_roc(df)
        df = compute_bollinger_width(df)
        df = compute_test_tost(df)
    except Exception as e:
        print(f"[{ahora_min}] ⚠️ Error al calcular indicadores: {e}. Saltando este ciclo.")
        return

    # 7) Condiciones de ENTRADA LARGO (A, B, C, D, E, F, G, H, I)
    max_prev  = df["open"].shift(1).combine(df["close"].shift(1), max)
    low_prev  = df["low"].shift(1)
    high_prev = df["high"].shift(1)

    condA = max_prev < (low_prev + (high_prev - low_prev) / CONDICION1)

    min_now = df[["open", "close"]].min(axis=1)
    condB1  = min_now > max_prev
    cuerpo  = (df["open"] - df["close"]).abs()
    rango   = (df["high"] - df["low"]).replace(0, 1e-8)
    condB2  = (cuerpo / rango) > 0.30
    condB   = condB1 & condB2

    condC = df["close"] > df["Live"]
    condD = ~df["crisis"]
    condF = df["close"] > df["upper_keltner"]
    condG = df["TOST"]
    condH = df.index.month != 9
    condI = df.index.dayofweek != 1

    df["long_entry"] = condA & condB & condC & condD & condF & condG & condH & condI

    # 8) Última vela de 1m
    last_ts  = df.index[-1]
    last_row = df.iloc[-1]

    # 9) Si no hay posición abierta y hay señal de LONG, abrimos
    if not long_on_market and last_row["long_entry"]:
        price_to_buy = last_row["close"]
        trade_size   = FIXED_CONTRACTS

        msg = (
            f"📈 <b>SEÑAL LARGO (1m)</b> en {symbol}\\n"
            f"Hora: {last_ts}\\n"
            f"Precio: {price_to_buy:.2f}\\n"
            f"Tamaño: {trade_size}"
        )
        print(f"[{last_ts}] {msg.replace('<b>', '').replace('</b>', '')}")
        telegram_send_message(msg)

        try:
            order = exchange.create_market_buy_order(symbol, trade_size)
            entry_price    = price_to_buy
            entry_index    = last_ts
            long_on_market = True
            print(f"[{last_ts}] 📈 → MARKET BUY ejecutada: {trade_size} {symbol} a {price_to_buy:.2f}")
            telegram_send_message(f"✅ <b>COMPRA EJECUTADA (1m)</b>: {trade_size} {symbol} a {price_to_buy:.2f}")
        except Exception as e:
            print(f"[{last_ts}] ⚠️ Error al enviar MARKET BUY: {e}")
            error_text = html.escape(str(e))
            telegram_send_message(f"⚠️ <b>Error MARKET BUY (1m)</b>: \\n<code>{error_text}</code>")
        return

    # 10) Si ya hay posición abierta, chequeamos salidas:
    if long_on_market:
        barras_en_trade = len(df.loc[entry_index : last_ts]) - 1
        stop_price_base = entry_price * (1 - LONG_STOP_PERCENT / 100)

        if barras_en_trade <= 1:
            target_price = entry_price * (1 + INITIAL_PROFIT_PERCENT / 100)
            stop_price   = stop_price_base
        elif barras_en_trade <= N_BARS:
            target_price = entry_price * (1 + LONG_PROFIT_PERCENT / 100)
            stop_price   = stop_price_base
        else:
            target_price = entry_price * (1 + (LONG_PROFIT_PERCENT / 2) / 100)
            stop_price   = entry_price * (1 - (LONG_STOP_PERCENT / 2) / 100)

        max_price_reached = df["high"].loc[entry_index : last_ts].max()

        # 10.1) Cierre por ROC < -0.90
        if last_row["roc_2"] < -0.90:
            msg = (
                f"📉 <b>CIERRE POR ROC (1m)</b>\\n"
                f"Hora: {last_ts}\\n"
                f"ROC2 = {last_row['roc_2']:.2f} < -0.90"
            )
            print(f"[{last_ts}] {msg.replace('<b>', '').replace('</b>', '')}")
            telegram_send_message(msg)
            try:
                order = exchange.create_market_sell_order(symbol, trade_size)
                print(f"[{last_ts}] 📉 → MARKET SELL (ROC) ejecutada: {trade_size} {symbol}")
                telegram_send_message(f"✅ <b>VENTA EJECUTADA (ROC – 1m)</b>: {trade_size} {symbol}")
            except Exception as e:
                print(f"[{last_ts}] ⚠️ Error MARKET SELL (ROC): {e}")
                error_text = html.escape(str(e))
                telegram_send_message(f"⚠️ <b>Error MARKET SELL (ROC 1m)</b>: \\n<code>{error_text}</code>")
            long_on_market = False
            entry_index    = None
            entry_price    = None
            trade_size     = None
            return

        low_today = last_row["low"]
        trailing_dynamic = last_row["close"] * (L_TRAILING_PERCENT / 100) / 85
        trailing_stop_price = max_price_reached - trailing_dynamic

        # 10.2) Cierre por Stop-loss o Trailing dinámico
        if low_today <= stop_price or low_today <= trailing_stop_price:
            if low_today <= trailing_stop_price:
                motivo      = "TRAILING"
                precio_ejec = trailing_stop_price
            else:
                motivo      = "STOP-LOSS"
                precio_ejec = stop_price

            msg = (
                f"📉 <b>CIERRE POR {motivo} (1m)</b>\\n"
                f"Hora: {last_ts}\\n"
                f"Precio ejecución ≈ {precio_ejec:.2f}"
            )
            print(f"[{last_ts}] {msg.replace('<b>', '').replace('</b>', '')}")
            telegram_send_message(msg)
            try:
                order = exchange.create_market_sell_order(symbol, trade_size)
                print(f"[{last_ts}] 📉 → MARKET SELL ({motivo}) ejecutada: {trade_size} {symbol}")
                telegram_send_message(f"✅ <b>VENTA EJECUTADA ({motivo} 1m)</b>: {trade_size} {symbol}")
            except Exception as e:
                print(f"[{last_ts}] ⚠️ Error MARKET SELL ({motivo}): {e}")
                error_text = html.escape(str(e))
                telegram_send_message(f"⚠️ <b>Error MARKET SELL ({motivo} 1m)</b>: \\n<code>{error_text}</code>")
            long_on_market = False
            entry_index    = None
            entry_price    = None
            trade_size     = None
            return

        # 10.3) Cierre por Take-profit
        high_today = last_row["high"]
        if high_today >= target_price:
            msg = (
                f"📈 <b>CIERRE POR TAKE-PROFIT (1m)</b>\\n"
                f"Hora: {last_ts}\\n"
                f"Target = {target_price:.2f}"
            )
            print(f"[{last_ts}] {msg.replace('<b>', '').replace('</b>', '')}")
            telegram_send_message(msg)
            try:
                order = exchange.create_market_sell_order(symbol, trade_size)
                print(f"[{last_ts}] 📈 → MARKET SELL (TP) ejecutada: {trade_size} {symbol}")
                telegram_send_message(f"✅ <b>VENTA EJECUTADA (TP 1m)</b>: {trade_size} {symbol}")
            except Exception as e:
                print(f"[{last_ts}] ⚠️ Error MARKET SELL (TP): {e}")
                error_text = html.escape(str(e))
                telegram_send_message(f"⚠️ <b>Error MARKET SELL (TP 1m)</b>: \\n<code>{error_text}</code>")
            long_on_market = False
            entry_index    = None
            entry_price    = None
            trade_size     = None
            return

    # 11) Si no se cumple ninguna, termina este ciclo
    print(f"[{last_ts}] 🛑 Ciclo finalizado sin señal.")

def job_every_minute():
    """
    Función invocada por schedule cada minuto.
    """
    try:
        strategy_1m_loop(SYMBOL, CAPITAL_USD)
    except Exception as e:
        now = pd.Timestamp.now(tz="Europe/Madrid")
        print(f"[{now}] ⚠️ ¡ERROR en strategy_1m_loop!: {e}")
        error_text = html.escape(str(e))
        telegram_send_message(f"⚠️ <b>Error en strategy_1m_loop</b>: \\n<code>{error_text}</code>")

if __name__ == "__main__":
    print("🚀 === BOT 1m DE KRAKEN FUTURES (MIX) INICIADO… ===")
    schedule.every().minute.do(job_every_minute)
    while True:
        schedule.run_pending()
        time.sleep(5)
