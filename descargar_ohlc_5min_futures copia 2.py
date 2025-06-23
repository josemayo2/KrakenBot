import subprocess
import time
import os
from datetime import datetime

RENKO_SCRIPT = "renko_kraken.py"
PYTHON_PATH = "python3"
MAX_RETRIES = 10
RETRY_DELAY = 30  # segundos entre reintentos


def log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[SUPERVISOR] ⏱️ [{now}] {msg}")


def lanzar_renko():
    log("🎯 Lanzando script RENKO...")
    return subprocess.Popen([PYTHON_PATH, RENKO_SCRIPT])


def main():
    retries = 0
    proceso = lanzar_renko()

    while True:
        time.sleep(5)
        retcode = proceso.poll()

        if retcode is None:
            continue  # sigue vivo

        log(f"⚠️ Proceso RENKO finalizó con código: {retcode}")
        retries += 1

        if retries >= MAX_RETRIES:
            log("❌ Número máximo de reintentos alcanzado. Abortando supervisor.")
            break

        log(f"🔁 Reintentando en {RETRY_DELAY} segundos... (Intento {retries}/{MAX_RETRIES})")
        time.sleep(RETRY_DELAY)
        proceso = lanzar_renko()


if __name__ == "__main__":
    main()
