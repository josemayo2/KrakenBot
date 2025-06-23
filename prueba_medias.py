import ccxt
import pandas as pd
import time
from ccxt.base.errors import NetworkError, ExchangeError

# ============================
# 1. CREDENCIALES KRAKEN FUTURES
# ============================
API_KEY    = 'TU_API_KEY_AQUÍ'
API_SECRET = 'TU_API_SECRET_AQUÍ'

# ============================
# 2. PARÁMETROS GLOBALES
# ============================
SYMBOL           = "BTC/USD:USD"   # Kraken Futures (fraccional 1m)
TIMEFRAME        = "1m"
SMA_SHORT_PERIOD = 2               # Para tu prueba “2 vs 5”
SMA_LONG_PERIOD  = 5
ORDER_SIZE       = 0.001
FETCH_LIMIT      = 100
SLEEP_SECONDS    = 10

# ============================
# 3. CONEXIÓN A KRAKEN FUTURES (CCXT)
# ============================
exchange = ccxt.krakenfutures({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
})

# ============================
# 4. ESTADO GLOBAL
# ============================
last_candle_time = None    # Timestamp de la última vela procesada
last_signal      = None    # "long" o "short" de la última señal
last_df_valid    = None    # Última DataFrame con datos válidos (usar si fetch falla)

# ============================
# 5. FUNCIONES AUXILIARES
# ============================

def fetch_ohlcv_ccxt(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    """
    Obtiene OHLCV de Kraken Futures, con retry en caso de timeout/5xx.
    Si falla 3 veces devuelve DataFrame vacío.
    """
    intentos = 0
    while intentos < 3:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["time"] = pd.to_datetime(df["timestamp"], unit="ms")
            return df[["time", "open", "high", "low", "close", "volume"]]
        except (NetworkError, ExchangeError) as e:
            intentos += 1
            print(f"[{pd.Timestamp.now()}] WARN: fetch_ohlcv error ({e}). Reintento {intentos}/3 en 1s...")
            time.sleep(1)
    # Si tras 3 intentos sigue fallando:
    print(f"[{pd.Timestamp.now()}] ERROR: No se pudieron obtener velas tras 3 intentos. DataFrame vacío.")
    return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])


def get_current_position_ccxt(symbol: str) -> float:
    """
    Consulta la posición neta. + = largo, - = corto, 0 = flat.
    """
    try:
        positions = exchange.fetch_positions([symbol])
        for pos in positions:
            if pos.get("symbol") == symbol:
                return float(pos.get("contracts", 0))
    except Exception as e:
        print(f"[{pd.Timestamp.now()}] ERROR fetch_positions: {e}")
    return 0.0


def place_market_order_ccxt(symbol: str, side: str, size: float) -> dict:
    """
    Envía orden MARKET a Kraken Futures.
    side = "buy" o "sell", size fraccional (0.001).
    """
    order = exchange.create_order(symbol, "market", side, size)
    return order


# ============================
# 6. LÓGICA DE CRUCE SMA 2/5 CON DEBUG Y BACKUP DE df
# ============================

def check_sma_crossover():
    global last_candle_time, last_signal, last_df_valid

    # 1) Intentamos leer velas
    df = fetch_ohlcv_ccxt(SYMBOL, TIMEFRAME, FETCH_LIMIT)

    # 2) Si df está vacío, usamos la última df válida (si existe). Si no existe última válida, salimos.
    if df.empty:
        if last_df_valid is None:
            print(f"[{pd.Timestamp.now()}] Aviso: sin datos de velas y no hay df previa. Se omite iteración.")
            return
        else:
            print(f"[{pd.Timestamp.now()}] Aviso: fetch devolvió DataFrame vacío. "
                  f"Usando última df válida (timestamp hasta {last_df_valid['time'].iloc[-1]}).")
            df = last_df_valid.copy()
    else:
        # Guardamos esta df como “válida” para reusar si falla la próxima vez
        last_df_valid = df.copy()

    # 3) Verificar que hay al menos SMA_LONG_PERIOD + 1 velas
    if len(df) < SMA_LONG_PERIOD + 1:
        print(f"[{pd.Timestamp.now()}] Esperando más velas para SMA (actuales: {len(df)})")
        return

    # 4) Calcular SMA2 y SMA5
    df["sma_short"] = df["close"].rolling(window=SMA_SHORT_PERIOD).mean()
    df["sma_long"]  = df["close"].rolling(window=SMA_LONG_PERIOD).mean()

    # 5) Tomar la última vela y la anterior
    last_row = df.iloc[-1]
    prev_row = df.iloc[-2]

    # 6) *** DEBUG PRINT: mostrar las últimas 5 filas con valores de close, sma_short, sma_long
    print("\n–––––– Últimas 5 filas (con SMA2/SMA5) ––––––")
    print(df.tail(5)[["time", "close", "sma_short", "sma_long"]])
    print("––––––––––––––––––––––––––––––––––––––––––––\n")

    # 7) Si es la primera iteración válida, guardamos y salimos
    if last_candle_time is None:
        last_candle_time = last_row["time"]
        print(f"[{pd.Timestamp.now()}] Vela inicial: {last_candle_time}. Esperando siguiente.")
        return

    # 8) Si la última vela NO ha cambiado, salimos
    if last_row["time"] == last_candle_time:
        print(f"[{pd.Timestamp.now()}] Aún misma vela ({last_row['time']}). Sin acción.")
        return

    # 9) Actualizamos last_candle_time (nueva vela cerrada)
    last_candle_time = last_row["time"]

    # 10) Detectar cruce 2/5:
    signal = None
    if (prev_row["sma_short"] < prev_row["sma_long"]) and (last_row["sma_short"] > last_row["sma_long"]):
        signal = "long"
    elif (prev_row["sma_short"] > prev_row["sma_long"]) and (last_row["sma_short"] < last_row["sma_long"]):
        signal = "short"

    # 11) Si no hay cruce, reseteamos last_signal y salimos
    if signal is None:
        print(f"[{pd.Timestamp.now()}] Vela {last_candle_time} sin cruce. Sin acción.\n")
        last_signal = None
        return

    # 12) Si ya procesamos esa señal para esta vela, salimos
    if signal == last_signal:
        print(f"[{pd.Timestamp.now()}] Señal '{signal}' ya procesada para {last_candle_time}. Sin acción.\n")
        return

    # 13) Consultar posición actual
    current_qty = get_current_position_ccxt(SYMBOL)
    print(f"[{pd.Timestamp.now()}] Posición actual = {current_qty} contratos.")

    # 14) Enviar orden según la señal
    if signal == "long":
        if current_qty <= 0:
            print(f"[{pd.Timestamp.now()}] ▶ Señal LARGO detectada. Enviando BUY {ORDER_SIZE}.")
            try:
                order = place_market_order_ccxt(SYMBOL, "buy", ORDER_SIZE)
                print(f"[{pd.Timestamp.now()}] ✅ Orden LONG ejecutada: {order}\n")
            except Exception as e:
                print(f"[{pd.Timestamp.now()}] ❌ Error al enviar orden LONG: {e}\n")
            last_signal = "long"
        else:
            print(f"[{pd.Timestamp.now()}] ℹ Ya hay posición larga ({current_qty}). No repetir orden.\n")
            last_signal = "long"

    elif signal == "short":
        if current_qty >= 0:
            print(f"[{pd.Timestamp.now()}] ▶ Señal CORTO detectada. Enviando SELL {ORDER_SIZE}.")
            try:
                order = place_market_order_ccxt(SYMBOL, "sell", ORDER_SIZE)
                print(f"[{pd.Timestamp.now()}] ✅ Orden SHORT ejecutada: {order}\n")
            except Exception as e:
                print(f"[{pd.Timestamp.now()}] ❌ Error al enviar orden SHORT: {e}\n")
            last_signal = "short"
        else:
            print(f"[{pd.Timestamp.now()}] ℹ Ya hay posición corta ({current_qty}). No repetir orden.\n")
            last_signal = "short"


# ============================
# 7. BUCLE PRINCIPAL
# ============================

if __name__ == "__main__":
    print("▶ Iniciando bot de SMA 2/5 (1m) en Kraken Futures con 0.001 contratos.")
    while True:
        try:
            check_sma_crossover()
        except Exception as e:
            print(f"[{pd.Timestamp.now()}] ERROR crítico en bucle principal: {e}")
        time.sleep(SLEEP_SECONDS)
