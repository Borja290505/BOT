# Resultados de la investigación del 2026-10-26

Ejecución: GitHub Actions «Investigación» nº 1 (commit d1892d4). Datos PF_XRPUSD
2022-03-23 → 2026-10-26, 40.276 velas de 1 h sin huecos. Comisiones del endpoint
(maker 0,02 %, taker 0,05 %), slippage 5 pb, sin funding. Plan: `PLAN.md`.

> Resultados simulados sobre datos pasados. No garantizan resultados futuros.

## Veredicto según el plan

**Ninguna configuración supera la regla de selección de la fase B.** La investigación
termina ahí: el holdout (últimos 12 meses) **no se ha tocado** y sigue disponible.
No se recomienda operar esta estrategia en real.

## Qué falla en la referencia (Donchian 20/10, 1 h)

| | Ops | Rentab. | Máx. DD | PF | Expect. R |
|---|---:|---:|---:|---:|---:|
| Con costes | 1.505 | −48,9 % | −67,8 % | 0,88 | −0,036 |
| Sin costes | 1.514 | +218,9 % | −25,5 % | 1,20 | +0,087 |

1. **Los costes se comen una ventaja pequeña.** Antes de comisiones y slippage la
   estrategia ganaba ~0,09 R por operación; los costes cuestan ~0,12 R. Con 27
   operaciones al mes, 4.393 USD de comisiones sobre 10.000 USD de capital.
2. **El stop está demasiado cerca para el ruido de 1 h.** 1.346 de 1.505 operaciones
   salen por stop (pérdida media −0,70 R); solo 141 llegan al take profit (+3,5 R).
3. **Depende del periodo.** 2022–2023 muy negativos; 2025 positivo. No es estable.
4. Largos y cortos pierden parecido: no se arregla quitando un lado.

## Cribado (solo desarrollo, hasta 2025-10-26)

| Configuración | Ops | Rentab. | Máx. DD | PF |
|---|---:|---:|---:|---:|
| 1h 20/10 | 1.177 | −45,1 % | −67,8 % | 0,87 |
| 1h 55/20 EMA 200 | 539 | −7,8 % | −44,3 % | 0,97 |
| 4h 20/10 | 281 | +6,6 % | −28,9 % | 1,05 |
| 4h 55/20 EMA 200 | 133 | −1,2 % | −20,2 % | 0,99 |
| **1d 20/10** | **43** | **+20,6 %** | **−4,8 %** | **1,98** |
| 1d 20/10 EMA 200 | 32 | +12,8 % | −6,6 % | 1,82 |
| 1d 55/20 | 19 | +8,1 % | −7,2 % | 1,67 |

(Tabla completa en el informe del workflow.)

- La tendencia es clara: **cuanto más lento el marco, menos costes y mejor resultado.**
  El filtro EMA 200 no ayuda de forma consistente.
- El marco diario tiene buen aspecto (PF ≈ 1,6–2,0 en las 4 variantes), pero con
  **solo 18–43 operaciones en 3,5 años** no es estadísticamente distinguible de la
  suerte, y por eso la regla (≥ 50 operaciones) lo excluye. No se relaja la regla
  después de ver el resultado.
- Incluso si fuera real, +20,6 % en 3,5 años ≈ 5,5 % anual con riesgo del 1 %.

## Siguiente experimento propuesto (no ejecutado; requiere decisión)

Para conseguir suficientes operaciones con un sistema diario, la vía estándar es
**diversificar**: el mismo Donchian diario sobre una cesta de perpetuos líquidos de
Kraken (p. ej. XBT, ETH, SOL, XRP, …), con riesgo repartido. Sería un plan nuevo,
prerregistrado, con el mismo holdout de 12 meses aún intacto. Implica que el bot
opere varios símbolos (hoy solo PF_XRPUSD), un cambio grande que no se hace sin
confirmación del usuario.
