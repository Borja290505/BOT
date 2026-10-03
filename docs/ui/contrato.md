# Contrato bot ↔ interfaz (Paso 4) · PROPUESTA pendiente de confirmación

> **Estado: propuesta.** La interfaz ya está construida contra este contrato y
> funciona entera con el proveedor de demostración. **No he tocado el bot.**
> Conectada al bot real, la interfaz solo lee hoy lo que el bot ya guarda (ver §1)
> y el control está desactivado. Los cambios del bot de la §5 se aplicarán solo
> cuando confirmes este documento.

## 0. Principios (tus condiciones)

1. **El proceso de la interfaz no tiene claves de Kraken ni habla con el exchange.**
   - No carga `.env`.
   - Lee la base de datos del bot en modo SQLite `mode=ro`.
   - Solo escribe en su propia base de datos de comandos.
2. **Lista cerrada de comandos:** `pause`, `resume`, `close_position`, `kill`,
   `rearm` y `reduce_param`. Cualquier otro tipo se rechaza en ambos lados.
3. **Cada comando lleva:**
   - `id` UUID único: es **idempotente**, y reenviar el mismo id no crea otro.
   - `created_at` y `expires_at` (TTL de 60 s). Un comando no recogido a tiempo
     **caduca y no se ejecuta nunca**.
4. **El bot valida siempre, aunque la interfaz ya lo haya hecho.** Nunca ejecuta
   un comando que aumente el riesgo.
5. **Todo queda en auditoría:** en la interfaz (`state/ui.sqlite`, tabla
   `audit`) y en el bot (tabla `commands` con su resultado y su tabla `events`).
6. **No se toca la lógica de estrategia ni de riesgo.** Los overrides solo
   pueden hacer los límites más estrictos.

## 1. Lo que la interfaz ya usa del bot (sin cambios en el bot)

Tablas existentes de `state/xrpbot-{modo}.sqlite`, en solo lectura:

| Tabla / clave | Uso en la interfaz |
|---|---|
| `trades` (cerradas) | Historial, detalle, CSV, P&L realizado |
| `orders`, `fills` | Cronología de cada operación |
| `events` | Registro y alertas (aproximadas) |
| `kv`: `dl.halted`, `dl.reason`, `dl.start_equity` | Estado DETENIDO y límite diario |
| `kv`: `bot.frozen` | Estado CONGELADO |
| `kv`: `trade.active_id` → `trades.state_json` | Posición activa: lado, tamaño, entrada, stop, TP |
| `data/PF_XRPUSD_*_1h.parquet` | Velas del gráfico y del backtest |

Sin la §5, la interfaz muestra «SIN ESTADO EN VIVO» y deja como «pendiente de
integración» todo lo demás: latido, mark price, equity, circuit breakers, salud
y control.

## 2. Estado en vivo: tabla `ui_status` (nueva, la escribe el bot)

```sql
-- en state/xrpbot-{modo}.sqlite (la escribe SOLO el bot)
CREATE TABLE IF NOT EXISTS ui_status (
  id INTEGER PRIMARY KEY CHECK (id = 1),   -- una sola fila, se sobrescribe
  updated_at TEXT NOT NULL,                -- ISO 8601 UTC
  snapshot_json TEXT NOT NULL              -- documento según contrato/snapshot.schema.json
);
```

- **Frecuencia:** cada 2 s, desde el bucle principal (`LiveBot.poll`). Escribir
  una fila en WAL cuesta microsegundos y no afecta al trading.
- **Formato:** `snapshot_json` cumple `docs/ui/contrato/snapshot.schema.json`
  (JSON Schema 2020-12). Los 10 escenarios de la demo lo validan en los tests.
- **Bot caído:** `bot.heartbeat_at` es el latido. Si tiene más de 15 s, la
  interfaz considera el bot **CAÍDO** y desactiva los controles. El bot no
  publica nunca `down`.
- **Campos que el bot ya calcula** y solo tendría que publicar:
  - Mercado: mark, last, bid, ask, spread, funding.
  - Cuenta: equity y referencia diaria.
  - Riesgo: valores y umbrales de cada circuit breaker, motivos de bloqueo.
  - Salud: estado del WS, desfase del reloj, última reconciliación.
- **Campos que el bot hoy NO mide** y habría que añadir:
  - Latencias REST p50/p95: medición alrededor de `_request`.
  - Puntos de rate limit usados: lectura del limitador existente.
  - Reconexiones del día: contador en `KrakenFuturesWS`.
  - Precio de liquidación de la posición abierta: hoy solo se estima antes de
    entrar.
  - Apalancamiento configurado en la cuenta: lectura periódica de
    `leveragepreferences`.

## 3. Comandos: base de datos `state/commands-{modo}.sqlite` (nueva)

Es un fichero aparte, para que la interfaz **nunca** escriba en la base de datos
del bot.

```sql
CREATE TABLE IF NOT EXISTS commands (
  id TEXT PRIMARY KEY,            -- UUID (idempotencia)
  type TEXT NOT NULL CHECK (type IN ('pause','resume','close_position','kill','rearm','reduce_param')),
  params_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,       -- created_at + 60 s
  requested_by TEXT NOT NULL,
  source_ip TEXT,
  status TEXT NOT NULL DEFAULT 'pending'
         CHECK (status IN ('pending','accepted','rejected','expired','done','failed')),
  result TEXT,
  processed_at TEXT
);
```

**Ciclo de vida:**
1. La interfaz inserta en `pending`. Antes ha comprobado: no está en solo
   lectura, el control está desbloqueado, el CSRF es válido, el comando está en
   la lista cerrada, la palabra de confirmación es correcta y el bot está vivo.
2. En cada ciclo (≤ 5 s), el bot lee los `pending` por orden de `created_at`:
   - **Caducado** (`now >= expires_at`): pasa a `expired` y no se ejecuta.
   - **Ya procesado** (mismo `id`): se ignora.
   - **Validación del bot** (§4): si falla, `rejected` con el motivo.
   - **Ejecución:** `done`, o `failed` con el motivo.
3. La interfaz consulta el resultado y lo muestra.

Esquema: `docs/ui/contrato/command.schema.json`.

## 4. Semántica y validación de cada comando (en el bot)

| Comando | Precondición (si no se cumple: `rejected`) | Efecto |
|---|---|---|
| `pause` | estado `active` (si ya está `paused`, `done` sin cambios) | No abre posiciones nuevas. La posición abierta mantiene su stop y TP. Persiste tras reinicios (`kv: ui.paused`) |
| `resume` | estado `paused` y sin bloqueos por override (§6) | Vuelve a abrir posiciones cuando hay señal. **No** sirve para salir de `halted` ni de `frozen` |
| `close_position` | hay posición del bot | `OrderManager.flatten(reason="cierre_manual")` y además **pausa** las entradas. Para seguir: `resume` |
| `kill` | ninguna | Lo mismo que `python -m xrpbot.cli kill`: cancela todo, cierra a mercado y deja la parada `KILL SWITCH` (rearme manual) |
| `rearm` | estado `halted` o `frozen`. **Rechazado** si la parada es por límite diario y es del mismo día UTC (la interfaz nunca se salta el límite diario; la CLI conserva `--force-same-day`). Rechazado si sigue habiendo una posición desconocida | Lo mismo que `python -m xrpbot.cli rearm` |
| `reduce_param` | `name` en la lista de la §6 y `value` estrictamente más prudente que el valor efectivo | Guarda el override en `kv: override.<name>` |

Además, `close_position` y `kill` llegan con la palabra escrita por el usuario
(`CERRAR` / `KILL`). La interfaz la comprueba; el bot no la necesita.

## 5. Cambios necesarios en el bot (NO aplicados)

| Archivo | Cambio | Riesgo |
|---|---|---|
| `xrpbot/state/db.py` | Tabla `ui_status` + `write_ui_status(json)` | Nulo (solo escritura de estado) |
| `xrpbot/live/status.py` (nuevo) | Construye la instantánea del §2 a partir del estado de `LiveBot` | Nulo (solo lectura) |
| `xrpbot/live/commands.py` (nuevo) | Lee `commands-{modo}.sqlite`, valida con `xrpbot_ui/contract.py` (lista cerrada, TTL, idempotencia, solo reducir) y ejecuta | **Medio:** ejecuta acciones; cubierto por tests |
| `xrpbot/live/runner.py` | En `poll()`: publicar el estado y procesar comandos. En `on_bar_close()`: no entrar si `ui.paused` | **Bajo:** dos llamadas y una condición |
| `xrpbot/live/runner.py` | Aplicar overrides al leer `risk_per_trade`, `max_daily_loss`, `max_leverage` y los umbrales de breakers: `efectivo = min/max(fichero, override)` | **Bajo:** solo puede endurecer |
| `xrpbot/exchange/rest.py`, `ws.py` | Métricas de latencia y contador de reconexiones | Nulo |
| `xrpbot/cli.py` | `rearm` también borra `ui.paused`. Nuevo `overrides --clear` para quitar overrides desde la CLI | Bajo |

Estimación: unas 300 líneas más tests. La lógica de estrategia, el tamaño de
posición, el OCO y la reconciliación no cambian.

## 6. Overrides de riesgo

- Parámetros reducibles (`xrpbot_ui/contract.py → REDUCIBLE_PARAMS`):
  - `risk.risk_per_trade`, `risk.max_daily_loss` y `risk.max_leverage`: solo bajar.
  - `circuit_breaker.max_spread_bps`, `max_data_age_s`, `max_candle_delay_bars`,
    `max_atr_ratio` y `max_mark_last_divergence_bps`: solo bajar.
  - `circuit_breaker.min_book_depth_multiple`: solo subir.
- **Valor efectivo:** el más estricto entre el fichero y el override.
  Sobrevive a reinicios (`kv` del bot).
- **Para aumentar** un valor: editar `config/settings.yaml` y reiniciar. Como el
  efectivo es el más estricto, un override anterior seguiría mandando; para
  quitarlo, `python -m xrpbot.cli overrides --clear` (CLI local, nunca desde la
  interfaz).
- **Si una reducción deja la posición abierta fuera de límites** (por ejemplo,
  baja el apalancamiento máximo por debajo del actual): el bot **no cierra**.
  Avisa (evento + Telegram) y bloquea las entradas nuevas hasta que esa
  posición se cierre.

## 7. Qué debe exponer el bot, en resumen

1. La fila `ui_status` cada 2 s, según `snapshot.schema.json`.
2. El procesamiento de `commands-{modo}.sqlite` según la §3 y la §4.
3. Los overrides de la §6.
4. Nada más: ni puertos, ni API HTTP, ni claves compartidas.

## 8. Preguntas para confirmar

1. ¿Te vale la integración por **SQLite** (estado en la base de datos del bot y
   comandos en un fichero aparte)? La alternativa es un socket local con una
   API, que añade un servidor dentro del bot; la desaconsejo.
2. ¿TTL de **60 s** para los comandos y latido de **2 s** con umbral de caído
   en **15 s**?
3. ¿La pausa debe **sobrevivir a reinicios** del bot? Propongo que sí.
4. ¿El kill switch desde la interfaz debe dejar la misma parada que el de la
   CLI (rearme manual)? Propongo que sí.
5. ¿Apruebas la lista de cambios de la §5 para que los implemente con sus tests?
