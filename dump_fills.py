#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import requests
import hashlib
import hmac
import base64
import json

# Tus credenciales de Futures
API_KEY    = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'

BASE_URL    = 'https://futures.kraken.com'
REQUEST_FILLS = '/derivatives/api/v3/fills'
SIGN_FILLS    = '/api/v3/fills'

def _get_nonce():
    return str(int(time.time()*1000))

def _sign(path, nonce):
    # padding
    sb = API_SECRET; pad = (-len(sb)) % 4
    if pad: sb += "="*pad
    sha = hashlib.sha256((nonce + path).encode()).digest()
    key = base64.b64decode(sb)
    mac = hmac.new(key, sha, hashlib.sha512).digest()
    return base64.b64encode(mac).decode()

def main():
    nonce = _get_nonce()
    auth  = _sign(SIGN_FILLS, nonce)
    url   = BASE_URL + REQUEST_FILLS
    headers = {'APIKey': API_KEY, 'Nonce': nonce, 'Authent': auth}
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    fills = r.json().get('fills', [])
    # Extraemos todos los símbolos únicos
    symbols = sorted({f.get('symbol') for f in fills})
    print("---- Distintos símbolos en fills ----")
    print(json.dumps(symbols, indent=2))
    print("\n---- Primeros 5 fills completos ----")
    print(json.dumps(fills[:5], indent=2))

if __name__ == "__main__":
    main()
