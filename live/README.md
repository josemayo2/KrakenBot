# Bot Squeeze Momentum en Kraken Futures (DigitalOcean)

Estrategia: la variante validada en walk-forward (`validate_squeeze.py`):
**velas de 4h, entrada al liberarse el squeeze tras ≥ 6 velas de compresión a favor del
momentum y de la EMA 200, salida cuando el momentum cruza cero, stop a 2 ATR.**
Mercados: BTC, ETH, SOL, XRP, ADA, LINK y DOGE (perpetuos `PF_*`).

El bot usa **el mismo código que el backtest** y hay un test (`tests/test_live.py`)
que lo ejecuta vela a vela y comprueba que hace exactamente las mismas operaciones.

## Cómo se dimensiona cada operación

No se usa un nocional fijo, sino **riesgo fijo**: si salta el stop se pierde el
`BOT_RISK_PCT` del capital (1% por defecto = 10 $ con 1.000 $). El nocional sale solo:

| Mercado | Stop típico (2 ATR en 4h) | Nocional con 1% de riesgo y 1.000 $ |
|---|---|---|
| BTC | ~2,5% | ~400 $ |
| SOL / DOGE | ~5–6% | ~170–200 $ |

Así cada operación arriesga lo mismo, que es como se hizo el backtest. Un nocional fijo
de 500 $ arriesgaría ~12 $ en BTC pero ~30 $ en DOGE.

Topes adicionales: 500 $ de nocional por operación, 2.500 $ en total, y si el capital
cae un 50 % desde su máximo el bot deja de abrir posiciones (sigue gestionando las abiertas).
Es un freno de emergencia: en la simulación 2022–2026 la peor caída fue del 36 %, y una
pausa al 25 % habría parado el bot para siempre en 2023.

## Instalación en el droplet (Ubuntu)

```bash
sudo timedatectl set-timezone UTC
sudo apt update && sudo apt install -y python3-venv git
git clone -b smc-strategy https://github.com/josemayo2/KrakenBot.git ~/squeeze-bot
cd ~/squeeze-bot
python3 -m venv .venv
.venv/bin/pip install pandas numpy requests ccxt
cp live/.env.example .env && chmod 600 .env
nano .env        # pega aquí las claves NUEVAS
```

Claves API de Kraken Futures: permiso de **trading** (acceso completo a órdenes),
**sin permiso de retirada**, y si Kraken lo permite, restringidas a la IP del droplet.

## Puesta en marcha por fases

**1. Paper (1–2 días):** verifica que cron y Telegram funcionan, sin claves.
```bash
.venv/bin/python -m live.squeeze_bot --mode paper
```

**2. Prueba de órdenes en DEMO** (cuenta en demo-futures.kraken.com, claves `KRAKEN_DEMO_*`):
```bash
.venv/bin/python -m live.squeeze_bot --mode demo --test-orders PF_XBTUSD
```
Debe terminar con `✅ Prueba de órdenes OK`. Comprueba compra, stop en el exchange,
cancelación y cierre.

**3. Prueba de órdenes en REAL** (cuesta céntimos: el tamaño mínimo de BTC):
```bash
.venv/bin/python -m live.squeeze_bot --mode live --test-orders PF_XBTUSD
```

**4. Activar el bot real con cron**, 2 minutos después de cada cierre de vela de 4h (UTC):
```bash
crontab -e
```
```
2 * * * * cd $HOME/squeeze-bot && flock -n /tmp/squeeze_bot.lock .venv/bin/python -m live.squeeze_bot --mode live --confirm-live >> live_state/bot.log 2>&1
```

## Día a día

```bash
.venv/bin/python -m live.squeeze_bot --mode live --status   # capital, posiciones, estado
tail -50 live_state/bot.log                                 # lo que ha hecho
cat live_state/trades_live.csv                              # registro de operaciones
```

- **Parar el bot:** comenta la línea de cron (`crontab -e`, poner `#` delante). Las posiciones
  abiertas conservan su stop en Kraken.
- **No toques a mano** las posiciones del bot. Si hay una posición que el bot no abrió en esos
  mercados, la ignora y avisa.
- **Actualizar el código:** `cd ~/squeeze-bot && git pull`.

## Qué esperar (y qué no)

- Gana el ~30–40 % de las operaciones. El beneficio viene de pocas operaciones grandes:
  **habrá rachas largas de pequeñas pérdidas**. Saltarse señales rompe la estrategia.
- El backtest no incluye el *funding* de los perpetuos; en real el resultado será algo peor.
- La validación fuera de muestra fue positiva pero **no concluyente** (t ≈ 1). El objetivo de
  los primeros meses es comparar lo real con lo simulado, no ganar dinero.
