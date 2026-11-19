"""Avisos por Telegram (opcional).

Solo usa las variables de entorno TELEGRAM_TOKEN y TELEGRAM_CHAT_ID. El código no lee
ningún archivo de claves. Si no están definidas, no hace nada.

En tu Mac puedes pasarlas desde tu .env al lanzar el comando, por ejemplo en cron:
    cd ~/Documents/KrakenBot && set -a && . ./.env && set +a && python3 -m ai_lens.paper --quiet
"""
from __future__ import annotations

import os
import urllib.parse
import urllib.request


def send(text: str) -> None:
    tok, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return
    body = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", body, timeout=15)
    except Exception as e:  # un fallo de Telegram nunca debe parar el paper trading
        print("telegram:", e)
