# logger_util.py
import datetime

ICONS = {
    'start':     '▶️',
    'cycle':     '🔄',
    'data':      '📊',
    'signal':    '🚨',
    'no_signal': '❌',
    'order':     '📈',
}

def stamp():
    return f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]"

def info(kind, msg):
    icon = ICONS.get(kind, '')
    print(f"{stamp()} {icon} {msg}")
