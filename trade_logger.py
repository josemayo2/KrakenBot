import os
import csv
from datetime import datetime

# Usar ruta absoluta junto al módulo para evitar problemas de cwd
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "all_trades.csv")
HEADERS = ["timestamp", "system", "signal", "price", "sl", "tp"]

# Para debug, imprime rutas al importar
print(f"[trade_logger] BASE_DIR = {BASE_DIR}")
print(f"[trade_logger] LOG_FILE = {LOG_FILE}")

# Si no existe, creamos el CSV con solo la cabecera
if not os.path.exists(LOG_FILE):
    with open(LOG_FILE, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(HEADERS)
    print(f"[trade_logger] Header creado en {LOG_FILE}")

def log_trade(system, signal, price, sl=None, tp=None):
    """
    Añade una línea al CSV de todas las señales ejecutadas.
    system: nombre del bot (p.ej. 'Ichimoku', 'RSI_kraken', 'Vol_ETH')
    signal: 'long' o 'short'
    price: float con el precio de ejecución
    sl, tp: stop loss y take profit (opcional)
    """
    # Construir la fila
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [ts, system, signal, price, sl or "", tp or ""]

    # Añadir al CSV
    with open(LOG_FILE, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(row)

    # Debug
    print(f"[trade_logger] Registro añadido: {row}")
