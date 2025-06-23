
import krakenex
import time
import csv
from collections import deque
from datetime import datetime

# Leer claves desde archivo
def leer_claves(path):
    claves = {}
    with open(path, 'r') as f:
        for linea in f:
            if '=' in linea:
                k, v = linea.strip().split('=', 1)
                claves[k.strip()] = v.strip()
    return claves

claves = leer_claves('kraken.key')
api = krakenex.API(claves['key'], claves['secret'])

# Configuración
par = 'XRP/EUR'
cantidad = 50.0
sma_periods = [5, 8, 13]
precios = deque(maxlen=max(sma_periods))

csv_file = 'ordenes_reales.csv'
estado_file = 'estado.txt'

# Inicializar CSV si no existe
if not Path(csv_file).exists():
    with open(csv_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Hora', 'Precio', 'SMA5', 'SMA8', 'SMA13', 'Señal', 'Resultado'])

# Inicializar archivo de estado si no existe
estado = 'ninguna'
if Path(estado_file).exists():
    with open(estado_file, 'r') as f:
        estado = f.read().strip()

def calcular_sma(datos, periodo):
    if len(datos) < periodo:
        return None
    return sum(list(datos)[-periodo:]) / periodo

print(f"[{time.strftime('%H:%M:%S')}] [Sistema] En funcionamiento... esperando condiciones.")

while True:
    try:
        ticker = api.query_public('Ticker', {'pair': par.replace('/', '')})
        if 'result' not in ticker:
            time.sleep(120)
            continue

        result = list(ticker['result'].values())[0]
        precio = float(result['c'][0])
        precios.append(precio)

        hora = time.strftime('%H:%M:%S')
        print(f"[{hora}] [Datos] Precio recogido: {precio:.5f} (esperando SMA completa)")

        if len(precios) >= max(sma_periods):
            sma5 = calcular_sma(precios, 5)
            sma8 = calcular_sma(precios, 8)
            sma13 = calcular_sma(precios, 13)

            if sma5 is None or sma8 is None or sma13 is None:
                continue

            nueva_senal = 'ninguna'
            if sma5 > sma8 > sma13:
                nueva_senal = 'compra'
            elif sma5 < sma8 < sma13:
                nueva_senal = 'venta'

            if nueva_senal == 'compra' and estado != 'compra':
                try:
                    respuesta = api.query_private('AddOrder', {
                        'pair': par.replace('/', ''),
                        'type': 'buy',
                        'ordertype': 'market',
                        'volume': cantidad
                    })
                    estado = 'compra'
                    print(f"[{hora}] 🟢 Operación ABIERTA (COMPRA)")
                    print(f"Precio de entrada: {precio:.5f}")
                    print(f"SMA5={sma5:.3f} | SMA8={sma8:.3f} | SMA13={sma13:.3f}")
                except Exception as e:
                    print(f"[{hora}] ❌ Error al ejecutar COMPRA: {e}")

            elif nueva_senal == 'venta' and estado != 'venta':
                try:
                    respuesta = api.query_private('AddOrder', {
                        'pair': par.replace('/', ''),
                        'type': 'sell',
                        'ordertype': 'market',
                        'volume': cantidad
                    })
                    estado = 'venta'
                    print(f"[{hora}] 🔴 Operación ABIERTA (VENTA)")
                    print(f"Precio de entrada: {precio:.5f}")
                    print(f"SMA5={sma5:.3f} | SMA8={sma8:.3f} | SMA13={sma13:.3f}")
                except Exception as e:
                    print(f"[{hora}] ❌ Error al ejecutar VENTA: {e}")

            with open(csv_file, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([hora, precio, sma5, sma8, sma13, nueva_senal.upper(), ''])

            with open(estado_file, 'w') as f:
                f.write(estado)

    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] ❌ Error en ciclo principal: {e}")

    time.sleep(120)  # cada 2 minutos para XRP
