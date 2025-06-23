#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auditar_kraken_futures.py

Auditoría de tus 5 sistemas de trading en Kraken Futures,
filtrando operaciones exclusivamente por clientOrderId.

Requisito:
- Cada bot debe enviar sus órdenes incluyendo un clientOrderId
  que comience con el nombre del sistema.
  Ejemplo: "renko_kraken-20250606-200003".

Con esto, el auditor mostrará únicamente los fills correspondientes a
cada sistema, sin necesidad de filtrar por símbolo.
"""

import time
import requests
import hashlib
import hmac
import base64
from tabulate import tabulate

# ——— TUS CREDENCIALES DE FUTURES ———
API_KEY    = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'
# ———————————————————————————

BASE_URL      = 'https://futures.kraken.com'
REQUEST_ACCTS = '/derivatives/api/v3/accounts'
SIGN_ACCTS    = '/api/v3/accounts'
REQUEST_FILLS = '/derivatives/api/v3/fills'
SIGN_FILLS    = '/api/v3/fills'

SISTEMAS = [
    "ichimoku_kraken_telegram_v2",
    "konkorde_live_telegram",
    "mix_kraken",
    "renko_kraken",
    "bollinger_kraken",
]


def _get_nonce() -> str:
    return str(int(time.time() * 1000))


def _sign(path: str, nonce: str) -> str:
    # Ajustar padding del Secret
    secret_b64 = API_SECRET
    pad = (-len(secret_b64)) % 4
    if pad:
        secret_b64 += "=" * pad
    # SHA256(nonce + path)
    msg = (nonce + path).encode()
    sha = hashlib.sha256(msg).digest()
    # HMAC-SHA512 con la clave decodificada
    key = base64.b64decode(secret_b64)
    mac = hmac.new(key, sha, hashlib.sha512).digest()
    return base64.b64encode(mac).decode()


def _private_get(request_path: str, sign_path: str) -> dict:
    nonce = _get_nonce()
    auth  = _sign(sign_path, nonce)
    url   = BASE_URL + request_path
    headers = {'APIKey': API_KEY, 'Nonce': nonce, 'Authent': auth}
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()


def check_connection() -> (bool, str):
    try:
        r = _private_get(REQUEST_ACCTS, SIGN_ACCTS)
        if r.get('result') == 'error' or r.get('error'):
            return False, r.get('error') or r.get('result')
        return True, ''
    except Exception as e:
        return False, str(e)


def has_trades_for_system(sistema: str) -> (bool, list):
    r = _private_get(REQUEST_FILLS, SIGN_FILLS)
    fills = r.get('fills', [])
    # Filtrar fills por clientOrderId que comience con el nombre del sistema
    my_fills = [f for f in fills if f.get('clientOrderId', '').startswith(sistema)]
    return bool(my_fills), my_fills


def main():
    resultados = []
    for sistema in SISTEMAS:
        conectado, err = check_connection()
        if not conectado:
            resultados.append([sistema, "❌ No conectado", err, "-"])
            continue

        try:
            opero, fills = has_trades_for_system(sistema)
        except Exception as e:
            resultados.append([sistema, "✅ Conectado", f"Error: {e}", "-"])
            continue

        if not opero:
            resultados.append([sistema, "✅ Conectado", "No", "-"])
        else:
            # Obtener último fillTime
            ts_list = [f['fillTime'] for f in fills if 'fillTime' in f]
            last_ts = max(ts_list) if ts_list else "-"
            resultados.append([sistema, "✅ Conectado", "Sí", last_ts])

    print(tabulate(
        resultados,
        headers=["Sistema","Conexión","Ha operado?","Última operación"],
        tablefmt="github"
    ))


if __name__ == "__main__":
    main()
