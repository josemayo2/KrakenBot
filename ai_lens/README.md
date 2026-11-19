# AI Lens para KrakenBot

El sistema "indicador de TradingView + Claude decide LONG / SHORT / WAIT", **sin depender de TradingView**.

El indicador `ai-agent-lens.pine` ([roan-maverick/tradingview-indicator-for-ai-agents](https://github.com/roan-maverick/tradingview-indicator-for-ai-agents), MIT) solo hace cálculos con las velas. Aquí está portado a Python y se alimenta con las velas de Kraken. Así se puede reproducir y hacer backtests desde cualquier sitio (también desde Claude Code en la nube), y TradingView queda solo para mirar el gráfico.

| Sistema original (TradingView) | Aquí |
|---|---|
| `ai-agent-lens.pine` en el gráfico | `indicator.py`, el mismo cálculo vela a vela |
| `tradingview-mcp` + `tv stream tables` | `python -m ai_lens.live` (en vivo) y `replay --stream` |
| `tv replay start` | `python -m ai_lens.replay --start ... --end ...` |
| `AGENT_PROMPT.md` + Claude | `AGENT_PROMPT.md` (Claude lee los paquetes) y `agent.py` (las mismas reglas en código) |

No envía órdenes. Analiza y propone; la decisión es tuya.

## Instalación

```bash
pip install pandas numpy        # ya están en requirements.txt
```

Solo usa la API pública de Kraken (`futures.kraken.com` y `api.kraken.com`), sin claves.

## Uso

```bash
# Backtest BTC/ETH/SOL 4h desde 2023, con comisiones (5 bps) y deslizamiento (2 bps) por lado
python -m ai_lens.backtest
python -m ai_lens.backtest --htf D --policy half_be      # sesgo diario, 50 % en T1 y stop a break-even

# Reproducción de un periodo (solo con velas hasta --end: nada del futuro)
python -m ai_lens.replay --start 2026-11-01 --end "2026-11-18 00:00"

# Flujo de paquetes JSON, uno por vela (como "tv stream tables")
python -m ai_lens.replay --start 2026-11-01 --end "2026-11-18 00:00" --stream --delay 0.5

# Reproducción a ciegas para que decida Claude:
python -m ai_lens.replay --start 2026-11-01 --end "2026-11-18 00:00" --blind > setups.jsonl
#   -> Claude lee los setups uno a uno con AGENT_PROMPT.md y escribe decisiones.json
python -m ai_lens.replay --start 2026-11-01 --end "2026-11-18 00:00" --decisions decisiones.json

# En vivo: a cada cierre de 4h escribe ai_lens/packets/latest_<ACTIVO>.json
python -m ai_lens.live
```

Con el modo en vivo funcionando, abre Claude Code en el repo y pídele:
*"Usa ai_lens/AGENT_PROMPT.md como instrucciones. Lee ai_lens/packets/latest_SOL.json y dame la señal."*
Si defines `TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID`, también te avisa por Telegram en cada setup.

## Paper trading (cartera simulada, sin claves)

`paper.py` lleva una cartera simulada **desde el día en que la arrancas**. Compara cuatro estrategias en paralelo:

| Estrategia | Qué hace |
|---|---|
| `indicador` | todas las señales del indicador |
| `reglas` | agente de reglas (AGENT_PROMPT en código) |
| `reglas_htfD` | agente de reglas con sesgo del marco diario |
| `claude` | las decisiones de Claude, si se registran antes de que cierre la vela siguiente |

```bash
python -m ai_lens.paper init            # 1000 USD, 0,5 % de riesgo por operación, BTC ETH SOL 4h
python -m ai_lens.paper                 # actualizar: descarga velas, registra setups y recalcula
python -m ai_lens.paper informe         # ver el informe (también en ai_lens/paper/INFORME.md)
python -m ai_lens.paper pendientes      # setups que esperan la decisión de Claude
python -m ai_lens.paper decidir --activo SOL --vela 2026-11-20T04:00:00Z --decision SHORT --confianza 60 --dudas "RSI 27"
```

Cron en el Mac, cada hora. Si el Mac estaba dormido, la siguiente ejecución recupera las velas perdidas:

```
5 * * * * cd /Users/josemayo/Documents/KrakenBot && /usr/bin/python3 -m ai_lens.paper run --quiet >> logs/paper.log 2>&1
```

Avisos de Telegram (opcional): el código solo lee las variables de entorno `TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID`, nunca un archivo de claves. Para usar las de tu `.env`, añade `set -a && . ./.env && set +a &&` antes de `/usr/bin/python3` en la línea de cron.

Garantías:
- Solo precios públicos de Kraken. No hay ni una línea que envíe órdenes ni que use claves de Kraken.
- Solo velas cerradas. El rendimiento se recalcula desde el inicio en cada ejecución y da siempre lo mismo.
- Ejecutarlo dos veces seguidas no duplica nada (hay un bloqueo de archivo).
- Una decisión de Claude que llega tarde se guarda como `TARDE` y no opera. Una decisión ya registrada no se puede cambiar.
- `diario.jsonl` guarda cada setup con la decisión de cada estrategia y la hora en que se registró.

Cuántas operaciones hacen falta: el backtest da unas 230 operaciones al año con las tres monedas (todas las señales). Para distinguir +0,09R de 0 con algo de confianza hacen falta varios cientos. Unos meses de paper dicen si el sistema se comporta como en el backtest, no si es rentable.

### En la nube (lo que está funcionando)

Una tarea programada de Claude se ejecuta cada 4 horas, a los 12 minutos del cierre de cada vela (00, 04, 08, 12, 16 y 20 UTC). En cada ejecución:
1. Actualiza la cartera.
2. Claude decide los setups pendientes con `AGENT_PROMPT.md` y registra sus decisiones.
3. Exporta `ai_lens/paper/dashboard.json` y actualiza el panel de seguimiento.
4. Guarda la cartera en esta rama.

El backtest del panel sale de `python -m ai_lens.dashboard backtest` (`results/backtest.json`). La prueba de robustez completa está en `python -m ai_lens.robustez`.

## Qué calcula (igual que el Pine)

Por cada vela cerrada calcula las puntuaciones LONG y SHORT (0-100) con cinco factores de 20 puntos: tendencia (EMA 20/50/200), momento (RSI, MACD), fuerza (ADX/DMI), volumen y marco superior. También el régimen (TREND_UP / TREND_DOWN / RANGE / HIGH_VOLATILITY), la estructura (pivotes de 5 velas), los niveles (máximo y mínimo del día anterior, VWAP) y la anatomía de la vela.

Hay un setup cuando la puntuación cruza 70. Entonces fija la entrada al cierre, el stop a 1,5 ATR y los objetivos a 1,5R y 3R. Si una vela toca stop y objetivo a la vez, cuenta el stop. El paquete JSON sigue el esquema `ai-agent-lens/v1`.

Comprobado: el simulador del backtest reproduce exactamente el seguimiento de operaciones del indicador (349/356/333 operaciones de BTC/ETH/SOL con el mismo resultado). El modo en vivo, con 1500 velas de calentamiento, da las mismas puntuaciones que con todo el historial.

## Resultados (velas de 4h de Kraken Futures, datos hasta el 18-11-2026)

### 1. Reproducción del 1 al 17 de noviembre de 2026

Hubo 11 setups, todos SHORT (mercado bajista). Decidí a ciegas: cada paquete por separado, en orden, sin ver lo que pasó después. Mis decisiones están en `results/decisiones_claude_nov2026.json`.

| | Operaciones | Cerradas | Resultado | R neto |
|---|---|---|---|---|
| Indicador (todas las señales) | 11 | 9 | 6 en T2, 3 en stop, 2 abiertas | **+14,5R** |
| Claude a ciegas con AGENT_PROMPT | 3 (3 WAIT, 5 SKIP) | 3 | 2 en T2, 1 en stop | **+4,9R** |
| Agente de reglas (`agent.py`) | 3 | 3 | las mismas 3 que Claude | **+4,9R** |

En este periodo el filtro "inteligente" **empeoró** el resultado. Descartó por "nivel en el camino al T1" o "vela que no acompaña" cuatro operaciones que acabaron en +3R.

Aviso: antes de decidir vi los precios BTC del 18-19 de noviembre mientras probaba el código. Eso solo afecta a los 3 setups del día 17, que descarté por reglas y que quedan abiertos al final del periodo.

Noviembre fue bueno para el indicador todos los años: 2023 +5,3R, 2024 +8,7R, 2025 +20,4R, 2026 +15,4R. Era un mes con tendencia, justo cuando este sistema funciona.

### 2. Backtest largo: 1-1-2023 a 18-11-2026, BTC + ETH + SOL

| Variante | Operaciones | Aciertos | R neto | R/operación | Máx. drawdown | PF |
|---|---|---|---|---|---|---|
| Indicador, todas las señales | 885 | 29 % | +76,3R | +0,09R | **46R** | 1,12 |
| Agente de reglas | 222 | 27 % | +5,7R | +0,03R | 21,5R | 1,03 |
| Agente de reglas, sin filtro de niveles | 592 | 27 % | +3,3R | +0,01R | 49R | 1,01 |
| Agente de reglas + sesgo diario (`--htf D`) | 83 | 33 % | +20,6R | +0,25R | 7,9R | 1,35 |
| Indicador, 50 % en T1 + break-even | 885 | 44 % | +48,5R | +0,05R | 49R | 1,09 |

- De 47 meses, solo 24 fueron positivos. El peor fue julio de 2026 (−20,7R) y el mejor enero de 2026 (+23,7R).
- Racha máxima: **35 pérdidas seguidas**. Arriesgando un 1 % por operación, eso es un −30 % de la cuenta.
- La variante con sesgo diario es la mejor por operación, pero son solo 83 operaciones (casi planas en 2023-2024). Puede ser suerte.

### Conclusión

El sistema funciona y es reproducible, pero **la ventaja es pequeña**: unos +0,09R por operación antes de cualquier filtro, con caídas de hasta 46R. El "+9R en 17 días" de la publicación es real como dato de un mes con tendencia, no como rendimiento esperado. Antes de arriesgar dinero conviene:

1. Paper trading con `python -m ai_lens.live` durante varias semanas.
2. Arriesgar poco: con rachas de 35 pérdidas, un 1 % por operación es demasiado; mejor un 0,25-0,5 %.
3. Si se filtra con Claude, medirlo contra "todas las señales" en un periodo largo, no en un solo mes.

## Diferencias con TradingView

- **Volumen:** el factor de volumen depende del exchange. Con datos de Binance en TradingView las puntuaciones no coincidirán exactamente. Para comparar en TradingView usa el mismo perpetuo de Kraken.
- **Marco superior:** en un gráfico de 4h el valor por defecto (`240`) no es superior, así que el factor HTF vale 10 neutro para los dos lados (igual que en el Pine). `--htf D` usa el diario.
- **Pivotes con empate:** solo afectan a los campos de estructura, no a las puntuaciones ni a los setups.
- **EMA/RMA:** se siembran con la SMA, como en Pine. Con años de historial la diferencia es nula.

## Archivos

| Archivo | Qué es |
|---|---|
| `indicator.py` | Port del indicador y generación del paquete JSON |
| `ta.py` | EMA, RMA, RSI, MACD, DMI/ADX, ATR, percentrank y pivotes, al estilo Pine |
| `data.py` | Descarga de velas de Kraken (futuros desde 2022, spot últimas 720) con caché |
| `agent.py` | Reglas de AGENT_PROMPT en código: LONG / SHORT / WAIT / SKIP y tamaño |
| `backtest.py` | Simulador y estadísticas en R, con costes |
| `replay.py` | Reproducción, flujo de paquetes, modo ciego y evaluación de decisiones |
| `live.py` | Modo en vivo a cada cierre de vela |
| `paper.py` | Paper trading: cartera simulada de las cuatro estrategias |
| `notify.py` | Avisos por Telegram desde variables de entorno |
| `robustez.py` | Pruebas contra el azar, por periodos y de sensibilidad |
| `dashboard.py` | Exporta los datos del panel de seguimiento |
| `AGENT_PROMPT.md` | Instrucciones para Claude, con tus reglas |
| `ai-agent-lens.pine` | Indicador original para pegar en TradingView y verlo en el gráfico |
