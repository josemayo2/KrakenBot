import requests

TELEGRAM_TOKEN = '8127892735:AAGZDT0xOzLpV01ISH5LKN3wG7INEL12Xrk'
TELEGRAM_CHAT_ID = '925121458'
mensaje = "[TEST] 🔔 Mensaje de prueba desde bot Renko2 Maker"

resp = requests.post(
    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
    data={'chat_id': TELEGRAM_CHAT_ID, 'text': mensaje},
    timeout=10
)

print(resp.status_code)
print(resp.text)
