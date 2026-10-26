# Experimento 2 · Cesta diaria de perpetuos: plan fijado ANTES de ver resultados

Fecha: 2026-10-26. Origen: en el experimento 1, el marco diario fue el único con
ventaja aparente en XRP (PF 1,6–2,0), pero con muy pocas operaciones (18–43) para
saber si era real. Diversificar en varios contratos da las operaciones necesarias.

Objetivo del usuario, traducido a criterios medibles: **ganancia neta después de
todos los costes**, con caídas pequeñas, y que se mantenga con datos no usados
para diseñarla. El porcentaje de acierto NO es un criterio (se puede fabricar
alto perdiendo dinero).

## Universo (fijo)
PF_XBTUSD, PF_ETHUSD, PF_SOLUSD, PF_XRPUSD, PF_ADAUSD, PF_DOGEUSD, PF_LTCUSD,
PF_LINKUSD, PF_DOTUSD, PF_AVAXUSD, PF_BCHUSD, PF_XLMUSD.
Entra en la cesta un contrato si tiene ≥ 24 meses de datos antes del fin del
periodo de desarrollo. Los que no se puedan descargar se excluyen y se anotan.

## Datos, costes y cuenta
- Velas de 1 h de Kraken (API pública) desde 2022-01-01, agregadas a 1 día (UTC).
- Comisiones del endpoint, slippage 5 pb. **Funding incluido** si se descarga
  (suma de las tasas horarias de cada día); si un contrato no tiene funding, se anota.
- Capital repartido a partes iguales entre los contratos (subcuentas). En cada
  subcuenta: riesgo 1 % por operación, apalancamiento efectivo ≤ 2x, parada diaria 3 %.
  El nocional total nunca supera 2x el capital total.

## Particiones
- Desarrollo: hasta 12 meses antes del final de los datos.
- Holdout: últimos 12 meses. Una sola evaluación, solo si se supera la fase C.

## Fase B · 4 configuraciones fijas (solo desarrollo)
Donchian diario {20/10, 55/20} × {take profit 4 R, sin take profit}. Stop 2 ATR(14),
trailing por canal de salida, sin filtros.
Selección: mayor PF de la cartera entre las que tengan ≥ 100 operaciones y
drawdown ≥ −25 %. Si ninguna tiene PF ≥ 1,2, se termina sin tocar el holdout.

## Fase C · Validación de la elegida (solo desarrollo)
| Criterio | Umbral |
|---|---|
| Operaciones | ≥ 100 |
| Profit factor | ≥ 1,2 |
| Expectativa | ≥ 0,10 R |
| Drawdown máximo | ≤ 20 % |
| Sharpe diario anualizado | ≥ 0,8 |
| Trimestres naturales con ganancia | ≥ 60 % |
| Robustez: slippage 15 pb y comisiones ×1,5 | expectativa > 0 |
| Concentración: sin el contrato que más gana | PF ≥ 1,1 |
| Rentabilidad neta | > 0 |

## Fase D · Holdout (una vez)
Pasa si rentabilidad neta > 0, PF ≥ 1,1 y drawdown ≤ 20 %.

## Después
- Si pasa: se adapta el bot a varios contratos y va a SIM (no a real).
- Si no pasa: se documenta y no se usa. Sin modificar criterios a posteriori.

> Resultados simulados sobre datos pasados. No garantizan resultados futuros.
