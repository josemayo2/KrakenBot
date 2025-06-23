#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import requests
import hashlib
import hmac
import base64
import json

API_KEY    = 'Bs5X+dC77ioO3xTUl+L+7S7EzkT1RTeJcf5BOGI0Zd1xrBK/ojxuyHsb'
API_SECRET = 'gBvwaAYZE4GQdFvUB6vTLLTBtJlhA5oVp6QjZu3WemEKtos8LbCOzLlMLPxCH+INNFqNOsunBfSPaIAgdhkmHO1d'

BASE_URL    = 'https://futures.kraken.com'
REQ_FILLS   = '/derivatives/api/v3/fills'
SIGN_FILLS  = '/api/v3/fills'

def _get_nonce():
    return str(int(time.time()*1000))

def _sign(path, nonce):
    sb = API_SECRET; pad = (-len(sb)) % 4
    if pad: sb += "="*pad
    sha = hashlib.sha256((nonce+path).encode()).digest()
    key = base64.b64decode(sb)
    mac = hmac.new(key, sha, hashlib.sha512).digest()
    return base64.b64encode(mac).decode()

def main():
    nonce = _get_nonce()
    auth  = _sign(SIGN_FILLS, nonce)
    url   = BASE_URL + REQ_FILLS
    headers = {'APIKey': API_KEY, 'Nonce': nonce, 'Authent': auth}
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    fills = r.json().get('fills', [])

    # Extrae solo los fills que tengan clientOrderId y muéstralos
    with_id = [f for f in fills if f.get('clientOrderId')]
    print(f"Total fills con clientOrderId: {len(with_id)}\n")
    print(json.dumps(with_id, indent=2))

if __name__ == "__main__":
    main()
