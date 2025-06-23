
import time
import datetime
import os
import sys
import ccxt
import pandas as pd
import requests
# Registro en CSV global
from datetime import datetime
import csv
import os

BASE_DIR = "/Users/jose/Documents/KrakenBot"
LOG_FILE = os.path.join(BASE_DIR, "all_trades.csv")

def log_trade(strategy, side, price, quantity, symbol):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, mode="a", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([now, strategy, side, price, quantity, symbol])

# Telegram
def telegram_message(message):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": message
        }
        try:
            requests.post(url, data=data)
        except:
            pass

# Bot arranca
now = datetime.now().strftime('%d/%m %H:%M')

now = datetime.now().strftime('%H:%M:%S')

telegram_message("🟢 [BOLL] Bot activo, sin señales – " + now)

# Simular ejecución cada hora con espera
print("[BOLL] Bot activo, esperando próxima señal...")
while True:
    now = datetime.now().strftime('%d/%m %H:%M')
    telegram_message("🔎 [BOLL] Sin señales claras – " + now)
    print("Esperando 1h...")
    time.sleep(3600)