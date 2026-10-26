# Experimento 2 · Resultados (2026-10-26)

Ejecución: GitHub Actions «Investigación» nº 2 (commit 772639a). 12 perpetuos de
Kraken, datos 2022-03 → 2026-10-26 (DOGE desde 2022-06, XLM desde 2022-07).
Plan: `PLAN-2.md`.

> Resultados simulados sobre datos pasados. No garantizan resultados futuros.

## Veredicto según el plan

La elegida («Donchian diario 20/10 sin take profit») cumple 8 de 9 criterios pero
**falla uno: trimestres con ganancia 56 % (9 de 16) frente al 60 % exigido.**
Según el plan, **no pasa**; el holdout (últimos 12 meses) **sigue sin tocar**.
No se relaja el criterio después de ver el resultado.

## Cribado (desarrollo, hasta 2025-10-26)

| Configuración | Ops | Rentab. | Máx. DD | PF | Expect. R | Sharpe |
|---|---:|---:|---:|---:|---:|---:|
| 20/10 · TP 4R | 330 | +9,4 % | −2,2 % | 1,80 | 0,352 | 1,18 |
| **20/10 · sin TP** | **290** | **+13,8 %** | **−2,9 %** | **2,32** | **0,575** | **1,07** |
| 55/20 · TP 4R | 164 | +3,7 % | −2,6 % | 1,49 | 0,284 | 0,64 |
| 55/20 · sin TP | 137 | +8,5 % | −3,6 % | 2,25 | 0,810 | 0,74 |

## Validación de la elegida

| Criterio | Resultado | ¿Pasa? |
|---|---:|:---:|
| Operaciones ≥ 100 | 290 | ✔ |
| Profit factor ≥ 1,2 | 2,32 | ✔ |
| Expectativa ≥ 0,10 R | 0,575 | ✔ |
| Drawdown ≤ 20 % | −2,9 % | ✔ |
| Sharpe ≥ 0,8 | 1,07 | ✔ |
| Trimestres con ganancia ≥ 60 % | 56 % de 16 | ✖ |
| Robustez (15 pb, comisiones ×1,5) | 0,511 R | ✔ |
| Sin el contrato que más gana (XRP): PF ≥ 1,1 | 2,18 | ✔ |
| Rentabilidad neta > 0 | +13,8 % | ✔ |

Ganan 9 de 10 contratos que operaron (XRP, ADA, DOGE, SOL y AVAX los que más; LTC pierde).

## Limitaciones que hay que conocer (afectan a la lectura)

1. **Funding casi ausente en desarrollo.** La API v4 solo devolvió funding desde
   2025-10-15, es decir, solo del holdout. Los resultados de desarrollo no incluyen
   funding; en posiciones de varios días puede restar rentabilidad.
2. **BTC, ETH y BCH apenas operaron.** Con 10.000 USD repartidos en 12 subcuentas
   (~833 USD cada una), muchas señales no alcanzaban el tamaño mínimo del contrato
   (descartadas: BTC 120, ETH 124, BCH 100, SOL 80, LTC 50). En la práctica la
   cesta ha sido sobre todo de altcoins.
3. **Rentabilidad baja en términos absolutos:** +13,8 % en ~3,5 años (≈ 3,8 %/año),
   porque el tope de apalancamiento 2x reparte poco nocional por subcuenta. La
   caída máxima también es muy baja (−2,9 %).
4. **Con el capital actual del usuario (~15 USD) esta estrategia no es operable:**
   casi todas las señales quedarían por debajo del tamaño mínimo.

## Qué significa

Es, con diferencia, lo más prometedor encontrado: ventaja clara antes y después de
costes, diversificada y robusta a costes más altos. Pero no cumple todos los
criterios fijados y tiene dos limitaciones de datos (funding y tamaño mínimo).
Ir a dinero real con esto sería saltarse el plan.

## Posibles siguientes pasos (cada uno sería un experimento nuevo y prerregistrado)
- Repetirlo cuando haya más histórico de funding, o con funding estimado de otra fuente.
- Probarlo en SIM en tiempo real (requiere adaptar el bot a varios contratos):
  es la forma de obtener datos nuevos sin gastar el holdout.
