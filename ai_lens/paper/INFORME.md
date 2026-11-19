# Paper trading AI Lens

Inicio: **2026-11-19T12:00:00Z** (0.1 días) · actualizado 2026-11-19T15:01:58Z · BTC, ETH, SOL 4h · capital inicial 1000.0 USD · riesgo 0.5 % por operación · costes 5.0+2.0 bps por lado

Última vela procesada: BTC 2026-11-19T12:00:00Z, ETH 2026-11-19T12:00:00Z, SOL 2026-11-19T12:00:00Z

| Estrategia | Capital USD | Rentab. | Abierto USD | Cerradas | Aciertos | R neto | R/op | Máx. DD | PF | Decisiones en setups |
|---|---|---|---|---|---|---|---|---|---|---|
| indicador | 1000.00 | +0.00 % | +0.00 | 0 | - | +0.00 | +0.000 | 0.0 % | - | - |
| reglas | 1000.00 | +0.00 % | +0.00 | 0 | - | +0.00 | +0.000 | 0.0 % | - | - |
| reglas_htfD | 1000.00 | +0.00 % | +0.00 | 0 | - | +0.00 | +0.000 | 0.0 % | - | - |
| claude | 1000.00 | +0.00 % | +0.00 | 0 | - | +0.00 | +0.000 | 0.0 % | - | - |

Referencia del backtest 2023-2026 (indicador, todas las señales): +0,09R por operación, 29 % de aciertos, rachas de hasta 35 pérdidas seguidas. Con menos de ~100 operaciones cerradas el resultado del paper es sobre todo ruido.

- **indicador**: todas las señales del indicador
- **reglas**: agente de reglas (AGENT_PROMPT en código)
- **reglas_htfD**: agente de reglas con sesgo diario
- **claude**: Claude con AGENT_PROMPT (decisiones registradas a tiempo)
