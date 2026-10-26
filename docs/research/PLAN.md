# Investigación de la estrategia: plan fijado ANTES de ver resultados

Fecha: 2026-10-26. Motivo: el backtest de referencia (Donchian 20/10, 1 h, futuros)
dio −47,7 %, drawdown −67,8 %, PF 0,88 en 1.485 operaciones (4.332 USD de comisiones).

Este documento se escribe antes de ejecutar nada y no se modifica después.
Si un resultado invita a «probar otra cosa más», esa otra cosa es un experimento
NUEVO que gasta un holdout nuevo; no se mezcla con este.

## Datos y costes
- PF_XRPUSD, velas de trades de 1 h de la API pública de Kraken desde 2022-01-01.
  4 h y 1 d se obtienen agregando las de 1 h (alineadas a 00:00 UTC).
- Costes de FUTUROS: comisiones del endpoint (o respaldo maker 0,02 %, taker 0,05 %),
  slippage 5 pb. Sin funding (su descarga falla; tarea pendiente nº 1).
- Riesgo 1 %/operación, apalancamiento efectivo máx. 2x, parada diaria 3 %.

## Particiones
- **Desarrollo:** desde el inicio de los datos hasta 12 meses antes del final.
- **Holdout:** últimos 12 meses. Se evalúa UNA vez, solo si se supera la fase C.

## Fase A · Diagnóstico de la referencia (todos los datos)
Desglose por lado, año, motivo de salida, duración; resultado bruto vs costes;
resultado sin costes (¿hay ventaja antes de comisiones?).

## Fase B · Cribado (solo desarrollo), 12 configuraciones fijas
Marco {1 h, 4 h, 1 d} × canales {20/10, 55/20} × filtro de tendencia {no, EMA 200 velas}.
Fijos: stop 2 ATR(14), take profit 4 R, trailing por canal de salida.
El filtro EMA 200: largos solo si cierre > EMA, cortos solo si cierre < EMA.

**Regla de selección:** entre las que tengan ≥ 50 operaciones y drawdown ≥ −30 %,
la de mayor profit factor (desempate: Sharpe). Si ninguna tiene PF ≥ 1,1 en
desarrollo, la investigación TERMINA: no hay candidata y no se toca el holdout.

## Fase C · Validación de la candidata (solo desarrollo)
1. Walk-forward (train 12 m / test 3 m) en su marco y con su filtro, rejilla
   entrada {20, 55} × salida {10, 20} × stop {2, 3} ATR.
2. Robustez: slippage 15 pb y comisiones ×1,5 con los parámetros de la candidata.
3. Criterios del README §6 Fase 1 sobre el OOS concatenado: ≥ 100 operaciones,
   PF ≥ 1,2, expectativa ≥ 0,10 R, drawdown ≤ 20 %, Sharpe ≥ 0,8, ganancia en
   ≥ 60 % de ventanas, y expectativa > 0 en la prueba de robustez.

## Fase D · Holdout (una vez)
Solo si la fase C pasa TODOS los criterios. Parámetros de la candidata, sin cambios.
Pasa si PF ≥ 1,1 y drawdown ≤ 20 %.

## Qué se hace con el resultado
- Si D pasa: la candidata va a SIM (no a real) durante el plan de pruebas (Fase 2).
- Si falla cualquier fase: se documenta, la estrategia NO se usa y el bot sigue en SIM.
- En ningún caso se activa el modo real desde esta investigación: requiere las
  claves y la confirmación escrita del usuario.

> Resultados simulados sobre datos pasados. No garantizan resultados futuros.
