# Tareas pendientes (interfaz y bot)

| # | Tarea | Bloquea | Estado |
|---|---|---|---|
| 1 | Arreglar la descarga del funding histórico (`historicalfundingrates` devuelve error en Kraken). Hasta entonces, la sección «Con vs sin funding» del Backtest se muestra **deshabilitada** con la explicación. | Pantalla 6, comparación con/sin funding | Pendiente: falta el mensaje de error completo (ya se muestra tras el commit 53f4d67) |
| 2 | Diagnosticar el backtest de −47,7 % (Donchian 1 h) antes de cambiar de timeframe o estrategia. | Decisión de estrategia | Pendiente |
| 3 | Contrato bot ↔ interfaz (Paso 4): tablas de estado y de comandos, latido, pausa, cierre de posición y overrides de riesgo. **Requiere confirmación antes de tocar el bot.** | Estado en vivo y control desde la interfaz conectada al bot real (hoy: solo lectura de historial, paradas y posición) | **Esperando tu confirmación** de `docs/ui/contrato.md` |
