# supervisor_global.py — Mantiene vivos ichimoku, bollinger, renko y konkorde

import subprocess
import time
import os
from datetime import datetime

SCRIPTS = [
    "ichimoku_kraken_telegram_v2.py",
    "bollinger_kraken.py",
    "renko_kraken.py",
    "konkorde_live_telegram.py"
]

PROCESOS = {}

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

def log(msg):
    print(f"[SUPERVISOR] 🛡️ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {msg}")

def iniciar(script):
    log(f"Iniciando {script}…")
    logfile = open(os.path.join(LOG_DIR, f"{script}.log"), "a")
    proceso = subprocess.Popen(["python3", script], stdout=logfile, stderr=logfile)
    PROCESOS[script] = proceso

def reiniciar(script):
    log(f"Reiniciando {script}…")
    proceso = PROCESOS.get(script)
    if proceso and proceso.poll() is None:
        proceso.terminate()
        proceso.wait()
    iniciar(script)

def ciclo_supervision():
    for script in SCRIPTS:
        proceso = PROCESOS.get(script)
        if not proceso:
            iniciar(script)
        elif proceso.poll() is not None:
            log(f"⚠️ {script} se detuvo. Reiniciando…")
            iniciar(script)

def main():
    log("🟢 Supervisor global activado. Vigilando bots…")
    while True:
        try:
            ciclo_supervision()
        except Exception as e:
            log(f"❌ Error en el supervisor: {e}")
        time.sleep(60)  # Chequeo cada minuto

if __name__ == "__main__":
    main()
