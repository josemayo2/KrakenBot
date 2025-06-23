#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import requests
import hashlib
import hmac
import base64

# ——— TUS CREDENCIALES DE FUTURES ———
API_KEY    = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'
# ————————————————————————————

BASE_URL     = 'https://futures.kraken.com'
REQUEST_PATH = '/derivatives/api/v3/accounts'   # para la petición
SIGN_PATH    = '/api/v3/accounts'               # para la firma

def _get_nonce() -> str:
    return str(int(time.time() * 1000))

def _sign(path: str, nonce: str) -> str:
    # postData vacío en GET
    msg = nonce + path
    sha = hashlib.sha256(msg.encode()).digest()
    # ajustar padding en caso de falta
    secret_b64 = API_SECRET
    pad = (-len(secret_b64)) % 4
    if pad:
        secret_b64 += "=" * pad
    key = base64.b64decode(secret_b64)
    mac = hmac.new(key, sha, hashlib.sha512).digest()
    return base64.b64encode(mac).decode()

def test_connection():
    nonce = _get_nonce()
    auth  = _sign(SIGN_PATH, nonce)
    url   = BASE_URL + REQUEST_PATH
    headers = {
        'APIKey':  API_KEY,
        'Nonce':   nonce,
        'Authent': auth,
    }

    print(f"-> Llamando a {url}")
    try:
        r = requests.get(url, headers=headers, timeout=10)
        print("-> Status code  :", r.status_code)
        print("-> Response body:", r.text)
        r.raise_for_status()
        j = r.json()
        print("-> JSON recibido :", j)
        if j.get('error') or j.get('result') == 'error':
            print("❌ Error API:", j.get('error') or j.get('result'))
        else:
            print("✅ ¡Conectado! Datos de cuentas OK.")
    except Exception as e:
        print("❌ Excepción al conectar:", e)

if __name__ == "__main__":
    test_connection()
