# xrpbot: bot de trading para PF_XRPUSD (Kraken Futures)

Bot para el perpetuo lineal XRP/USD de Kraken Futures. Estrategia de ruptura
Donchian + stop por ATR, timeframe de 1 h, largos y cortos.

> **Aviso.** Esto es software experimental. No hay garantía de rentabilidad: la
> estrategia puede perder dinero, y con apalancamiento las pérdidas pueden ser
> rápidas. Úsalo primero en backtest, después en demo y solo después, si se
> cumplen los criterios del plan de pruebas, con poco capital real.

## Contenido

1. [Arquitectura](#1-arquitectura)
2. [Instalación](#2-instalación)
3. [Uso: backtest, demo, sim y real](#3-uso)
4. [Decisiones de diseño y sus riesgos](#4-decisiones-de-diseño-y-riesgos)
5. [Puntos a verificar contra la API real](#5-puntos-a-verificar-contra-la-api-real)
6. [Plan de pruebas y criterios para pasar a real](#6-plan-de-pruebas-y-criterios-para-pasar-a-real)

---

## 1. Arquitectura

```
xrpbot/
├── config.py               settings.yaml + .env; validación al arrancar; demo por defecto
├── models.py               tipos comunes (Side, InstrumentSpec, OrderRequest, Fill...)
├── exchange/
│   ├── auth.py             firma HMAC-SHA512 (REST y challenge del WS), nonce creciente
│   ├── rate_limiter.py     cubo de fichas por coste (500 puntos / 10 s, con margen)
│   ├── rest.py             cliente REST asíncrono: reintentos, backoff, desfase de reloj
│   ├── ws.py               ticker en vivo + avisos privados; reconexión con backoff
│   ├── instruments.py      /instruments -> tick, incremento de tamaño, márgenes
│   └── fees.py             /feeschedules -> comisiones maker/taker por tramo de volumen
├── data/                   descarga a Parquet, validación, contrato de velas
├── strategy/               indicadores sin look-ahead y estrategia Donchian + ATR (pura)
├── risk/
│   ├── sizing.py           tamaño por riesgo, tope de apalancamiento, mínimo/incremento
│   ├── limits.py           pérdida diaria (persistente, rearme manual), liquidación
│   └── circuit_breaker.py  spread, datos desfasados, volatilidad, mark/last, libro fino
├── execution/
│   ├── oco.py              estado de la operación + OCO propio (lógica pura)
│   ├── broker.py           interfaz Broker: KrakenBroker (demo/live) y PaperBroker (sim)
│   └── order_manager.py    envío idempotente (cliOrdId), trailing, cierre forzado
├── state/
│   ├── db.py               SQLite (WAL): estado, señales, órdenes, ejecuciones, operaciones
│   └── reconcile.py        reconciliación exchange <-> estado local (lógica pura)
├── backtest/               motor, métricas, walk-forward + holdout, regímenes
├── monitoring/             logging rotativo, alertas de Telegram (solo envío)
├── live/runner.py          bucle principal de demo / sim / live
└── cli.py                  todos los comandos
```

La estrategia, el tamaño de posición, los límites de riesgo, el OCO y la
reconciliación son el **mismo código** en backtest, sim, demo y real. Solo
cambian la fuente de datos y el broker. Así lo validado en el backtest es lo
que se ejecuta.

### Estrategia (A: Donchian + ATR, 1 h)

| Regla | Detalle |
|---|---|
| Entrada larga / corta | Cierre de la vela > máximo (o < mínimo) de las 20 velas **anteriores** |
| Ejecución | Orden limitada IOC con tope de deslizamiento (25 pb) sobre el mid, no una orden a mercado pura |
| Stop inicial | Precio de señal ∓ 2·ATR(14) |
| Take profit | Entrada ± 4R, recalculado sobre el precio real de ejecución; orden post-only reduce-only |
| Trailing | Al cierre de cada vela, el stop se mueve al mínimo (o máximo) de las 10 últimas velas; nunca retrocede |
| Filtro de funding (opcional) | No abre largos si el funding horario es > 0,01 %, ni cortos si es < −0,01 % |

---

## 2. Instalación

Requisitos: Python 3.11 o superior, reloj del sistema sincronizado (NTP) y
salida a internet hacia `futures.kraken.com`, `demo-futures.kraken.com` y,
opcionalmente, `api.telegram.org`.

```bash
git clone <repo> && cd BOT
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # y rellénalo (ver más abajo)
python -m pytest -q           # 105 tests, sin red
```

### Claves de API

1. **Demo:** crea una cuenta en https://demo-futures.kraken.com y genera allí las
   claves. Ponlas en `KRAKEN_DEMO_API_KEY` y `KRAKEN_DEMO_API_SECRET`.
2. **Real:** cuando toque, crea claves **distintas** en Kraken Futures con solo
   permisos de consulta y trading. **Nunca actives el permiso de retirada.**
   Restríngelas a la IP fija de tu servidor. Van en `KRAKEN_LIVE_API_*`.
3. El bot solo lee las claves del modo en el que corre: en demo nunca toca las
   de real.
4. `.env` está en `.gitignore`. No lo subas nunca a ningún repositorio.

### Telegram (opcional)

Crea un bot con @BotFather y rellena `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`.
Pon `alerts.telegram_enabled: true` en `config/settings.yaml`. Solo envía
alertas: señales, entradas, salidas, errores, paradas y kill switch. No acepta
comandos.

### Docker (VPS)

```bash
docker compose build
docker compose up -d bot-demo                      # demo, se reinicia solo
docker compose logs -f bot-demo
docker compose run --rm cli status --mode demo
docker compose run --rm cli kill --mode demo       # KILL SWITCH
```

El estado (`state/`), los datos (`data/`) y los logs (`logs/`) viven en volúmenes
del host, así que sobreviven a reinicios y a reconstrucciones del contenedor.

---

## 3. Uso

Todos los comandos: `python -m xrpbot.cli <comando> [opciones]`.

| Comando | Qué hace |
|---|---|
| `check [--mode demo\|live]` | Verifica instrumento, comisiones, ticker, desfase de reloj y la firma privada (lee la equity). **Ejecútalo lo primero.** |
| `download [--since 2022-01-01]` | Descarga velas de trades y de mark price de 1 h y el funding histórico a `data/` (Parquet). Es incremental. |
| `backtest [--funding-filter on\|off\|both] [--start --end]` | Backtest con métricas y desglose por régimen; CSV en `reports/`. |
| `walkforward [--funding-filter both] [--holdout]` | Validación walk-forward; `--holdout` evalúa el tramo final, **una sola vez**. |
| `run` | Arranca el bot en el modo de `settings.yaml` (**demo** por defecto). |
| `run --mode sim` | Simulador interno con datos públicos en vivo (no necesita claves). |
| `run --mode live --live` | Real: pide escribir `OPERAR EN REAL PF_XRPUSD`. |
| `kill --mode demo\|live` | **Kill switch:** cancela todas las órdenes, cierra la posición a mercado y deja el bot detenido. |
| `rearm --mode ... [--force-same-day]` | Rearme manual tras la parada diaria, un kill o una congelación. |
| `status --mode ...` | Parada, congelación, operación activa y últimos eventos. |
| `export --mode ... [--out reports/csv]` | Exporta a CSV las operaciones (con P&L realizado, comisiones y funding), ejecuciones, órdenes, señales y eventos. |

### Flujo recomendado

```bash
python -m xrpbot.cli check --mode demo
python -m xrpbot.cli download --since 2022-01-01
python -m xrpbot.cli backtest --funding-filter both
python -m xrpbot.cli walkforward --funding-filter both
# solo cuando la estrategia esté decidida y no se vaya a tocar más:
python -m xrpbot.cli walkforward --funding-filter off --holdout
python -m xrpbot.cli run                     # demo
```

### Modo real

- Exige `--mode live`, el flag `--live` y escribir la frase de confirmación.
- Sin terminal (Docker o systemd), la confirmación se da con la variable
  `XRPBOT_LIVE_CONFIRM="OPERAR EN REAL PF_XRPUSD"`. Es igual de explícita, pero
  queda en un fichero: no la dejes puesta si no vas a operar en real.
- En real, si no se pueden leer las comisiones del endpoint, el bot **no
  arranca**; en demo usa las de respaldo y avisa.
- Cada modo tiene su propia base de datos (`state/xrpbot-{modo}.sqlite`), así
  que demo, sim y real nunca se mezclan. Pasa siempre `--mode` a `kill`,
  `rearm`, `status` y `export`.

### Funcionamiento 24/7 en Windows

1. Añade al `.env` la confirmación del modo real (sustituye a escribir la
   frase en cada arranque; quítala si dejas de operar en real):
   ```
   XRPBOT_LIVE_CONFIRM=OPERAR EN REAL PF_XRPUSD
   ```
2. Evita que el PC se suspenda (cmd como administrador):
   ```bat
   powercfg /change standby-timeout-ac 0
   powercfg /change hibernate-timeout-ac 0
   ```
3. Arranca con reinicio automático: doble clic en `scripts\run_live.bat`, o
   desde la terminal `scripts\run_live.bat`. Si el bot se cae (corte de red,
   error), se vuelve a arrancar a los 60 s y reconcilia con Kraken.
4. Opcional, para que arranque al iniciar sesión: Programador de tareas ->
   Crear tarea básica -> "Al iniciar sesión" -> Iniciar un programa ->
   `scripts\run_live.bat` (con "Iniciar en" = la carpeta del bot).

Limitaciones: si el PC se apaga, se reinicia por actualizaciones de Windows o
pierde internet un rato, el bot no opera durante ese tiempo (las órdenes de
stop y TP siguen en Kraken). Para disponibilidad real 24/7, un VPS con Docker
(ver arriba).

### Comportamiento ante incidencias

| Situación | Reacción |
|---|---|
| Pérdida diaria ≥ 3 % (equity realizada + no realizada desde las 00:00 UTC) | Cierra todo, se detiene y exige `rearm`. Si la parada es del mismo día, `rearm` exige además `--force-same-day`. |
| Kill switch (`kill`) | Cancela órdenes, cierra a mercado y registra la parada. Si el bot sigue en marcha, ve la parada y no abre nada más. |
| Posición desconocida en el exchange | Se **congela**, avisa y, si no hay stop, pone un stop de emergencia al 5 %. |
| Lado de la posición contrario al esperado | Se congela y avisa. |
| Falta el stop o el TP en el exchange | Lo recoloca. |
| Tamaños distintos entre el exchange y el estado local | Adopta el tamaño del exchange y ajusta el stop y el TP. |
| El exchange rechaza el stop loss | **Cierra la posición a mercado**: nunca deja una posición sin stop. |
| Envío de orden ambiguo (timeout) | Busca la orden por `cliOrdId`. Si no aparece, reenvía con el **mismo** `cliOrdId`; el exchange rechaza el duplicado. |
| Reinicio a mitad de una vela ya procesada | El ID de la operación es determinista por vela: no repite la entrada. |
| WebSocket caído | Reconecta con backoff y reconcilia. El sondeo REST sigue funcionando mientras tanto. |
| Error inesperado en el código | Se congela; no sigue operando a ciegas. |
| Circuit breaker (spread > 15 pb, ticker > 30 s, ATR > 3× su mediana, divergencia mark/last > 50 pb, libro fino) | No abre posiciones nuevas; las abiertas siguen protegidas por su stop. |

---

## 4. Decisiones de diseño y riesgos

### Cliente de la API propio (aiohttp + websockets)
- **Por qué:** control total de `cliOrdId`, `reduceOnly`, `triggerSignal`, los
  reintentos y la idempotencia. Pocas dependencias en la ruta crítica de las
  órdenes. ccxt abstrae justo lo que hay que controlar, y python-kraken-sdk
  añade una dependencia sin aportar seguridad.
- **Riesgo:** si Kraken cambia un campo, hay que adaptarlo a mano. Lo mitigan el
  parseo defensivo, el comando `check` y la regla de que **el sondeo REST es la
  fuente de verdad** (el WS solo despierta al bucle).

### Stop disparado por mark price
- **Por qué:** Kraken liquida por mark price, y el mark resiste mejor las mechas
  de un libro fino. Disparar por last price en XRP saca de posiciones por picos
  de un solo trade.
- **Coste:** en un desplome rápido, el mark puede ir algo rezagado y el stop
  ejecutarse más lejos. El backtest usa velas de mark para disparar el stop.
  Puedes cambiarlo con `execution.stop_trigger_signal`.

### Stop a mercado (sin `limitPrice`), TP post-only
- Un stop-limit puede no ejecutarse en un hueco de precio y dejarte dentro. El
  stop a mercado garantiza la salida, pero **no el precio**.
- El TP post-only paga comisión maker. Si al colocarlo el precio ya lo ha
  superado, se toma el beneficio a mercado.

### Sin dead man's switch (`cancelallordersafter`)
- Cancela **todas** las órdenes, también el stop loss. Si el bot cae, la
  posición quedaría sin protección. Aquí las órdenes de protección viven en el
  exchange de forma independiente del bot.

### Tamaño de posición y apalancamiento
- Riesgo del 1 % = distancia al stop + comisiones y slippage estimados de ida y
  vuelta. El tamaño se redondea **hacia abajo** al incremento del contrato; si
  queda por debajo del mínimo, no se opera.
- Tope de apalancamiento efectivo sobre el nocional (`risk.max_leverage`, 10x
  por defecto, máximo permitido 10x). Solo actúa con stops muy cercanos: con
  10x, stops a menos del ~0,1 %; con 2x, a menos del 0,5 %. En ese caso se
  arriesga **menos** del 1 %. Subir el tope no aumenta el riesgo por
  operación, pero sí el tamaño de las posiciones con stop cercano y el daño de
  un hueco de precio.
- **El 1 % no es un máximo garantizado:** un hueco de precio puede saltarse el
  stop. El backtest lo modela: sale a la apertura si la vela abre más allá del
  stop.

### Liquidación y margen
- Se intenta fijar margen **aislado** al apalancamiento máximo configurado. Si no es posible, se opera en
  **cruzado** y se avisa: en ese caso deja en la cuenta de futuros solo el
  capital del bot.
- La liquidación estimada debe quedar al menos 3 veces más lejos que el stop.
  Es una estimación (Kraken usa tramos de margen). En aislado queda a ~49 % del
  precio con 2x y a ~9 % con 10x: con 10x, un movimiento brusco de XRP puede
  liquidar la posición, y el bot rechaza las entradas con stop a más del ~3 %.

### Funding
- Se incluye en el backtest como pago horario. En vivo, el campo `funding` de
  cada operación es una **estimación** con la misma fórmula; el funding real
  está en la equity de la cuenta, que es lo que vigila el límite diario.
- Una estrategia de tendencia suele ir con la mayoría y **pagar** funding en
  las fases eufóricas. El filtro evita entrar cuando el funding es extremo; el
  walk-forward compara con y sin él.

### Backtest
- Señal al cierre de la vela i y entrada a la apertura de la vela i+1.
- Si en la misma vela se tocan el stop y el TP, se asume el **stop**.
- El TP exige que el precio lo **supere**, no basta con tocarlo.
- Comisiones del endpoint, slippage de 5 pb por ejecución taker y funding
  horario.
- **Test de cordura:** sobre un paseo aleatorio sin costes, la expectativa debe
  ser ~0R; con costes, negativa. Está en la suite de tests. Detectó y descartó
  un sesgo optimista que venía de usar velas sintéticas poco realistas.
- **Limitaciones:**
  - Velas de 1 h: no se ve el orden de los precios dentro de la vela (de ahí
    los supuestos pesimistas).
  - El slippage real en momentos de pánico será mayor que 5 pb.
  - El límite diario del backtest se evalúa al cierre de cada vela; en vivo, cada
    pocos segundos.
  - El histórico del perpetuo es corto: el walk-forward tendrá pocas ventanas.

### Sobreajuste
- 3 parámetros optimizados en una rejilla de 12 combinaciones.
- Walk-forward rodante: 12 meses de entrenamiento y 3 de test.
- Tramo final de 6 meses intocable (holdout), que se evalúa una sola vez.
- Si los parámetros elegidos cambian mucho entre ventanas, la estrategia no es
  estable: no pases a real.

---

## 5. Puntos a verificar contra la API real

Este código se escribió sin acceso de red a Kraken: los dominios de Kraken
estaban bloqueados en el entorno de desarrollo. Todo lo siguiente se basa en
la documentación oficial y está aislado y señalado en el código. **Verifícalo
con `check` y en demo antes de nada:**

1. Símbolo `PF_XRPUSD`, `tickSize`, `contractSize`, `contractValuePrecision`
   (incremento y mínimo de tamaño) y `marginLevels`. `check` imprime los campos
   crudos.
2. La firma (`Authent`): la ruta se firma sin el prefijo `/derivatives`. Si
   `check` lee la equity, la firma está bien.
3. Unidades de `makerFee`/`takerFee` en `/feeschedules`. Se asume que vienen en
   porcentaje (0,02 = 0,02 %); hay una comprobación de rango que aborta si no
   cuadran.
4. Que `relativeFundingRate` de `/historicalfundingrates` sea la fracción del
   periodo **horario**. El backtest imprime la media del funding para detectar
   una escala absurda.
5. El campo de equity en `/accounts` (`flex.portfolioValue`, con
   `marginEquity`/`balanceValue` como alternativas).
6. Que `PUT /leveragepreferences` con `maxLeverage` ponga el símbolo en margen
   aislado.
7. Que una orden `stp` sin `limitPrice` y con `triggerSignal=mark` se ejecute
   como stop a mercado, y que `editorder` admita cambiar `stopPrice` y `size`.
8. Que una orden reduce-only que sobrevive a una posición ya cerrada no pueda
   abrir posición (el OCO propio depende de ello). **Pruébalo a mano en demo.**
9. El formato de los mensajes privados del WS (`fills`, `open_orders`). Solo
   se usan como aviso, así que un error aquí no rompe la lógica.
10. El máximo de velas por petición de la API de charts. El descargador pagina
    de forma defensiva.

---

## 6. Plan de pruebas y criterios para pasar a real

Los criterios son **objetivos y se fijan antes de ver los resultados**. Si no se
cumplen, no se pasa de fase. Cambiar la estrategia reinicia el plan desde la
fase 1 y gasta el holdout.

### Fase 0: tests y verificación de la API (1 día)
- `pytest`: los 105 tests en verde.
- `check --mode demo`: instrumento, comisiones, firma y desfase de reloj < 1 s.
- Los 10 puntos de la sección 5 verificados en demo. Haz a mano, con 1 contrato:
  entrada, stop, TP, cancelación, edición, una orden reduce-only con la posición
  ya cerrada y el kill switch.

### Fase 1: backtest y walk-forward (antes de cualquier demo)
Fuera de muestra (OOS, concatenación de los tramos de test del walk-forward):

| Criterio | Umbral |
|---|---|
| Número de operaciones OOS | ≥ 100 (con menos, nada es significativo) |
| Profit factor OOS | ≥ 1,2 |
| Expectativa OOS | ≥ 0,10R por operación, después de costes y funding |
| Drawdown máximo OOS | ≤ 20 % |
| Sharpe OOS (diario, anualizado) | ≥ 0,8 |
| Estabilidad | Ganancia en ≥ 60 % de las ventanas de test; parámetros elegidos parecidos entre ventanas |
| Robustez | Con slippage de 15 pb y comisiones +50 %, la expectativa sigue siendo > 0 |
| Regímenes | El resultado no depende de un solo régimen: sin la mayor tendencia, sigue sin colapsar |

Después, **una sola vez**, el holdout: profit factor ≥ 1,1 y drawdown ≤ 20 %.

### Fase 2: demo (mínimo 6 semanas y ≥ 30 operaciones; lo que llegue después)

| Criterio | Umbral |
|---|---|
| Incidencias operativas | 0 posiciones sin stop; 0 órdenes duplicadas; 0 congelaciones sin explicar |
| Disponibilidad | ≥ 99 % (logs y alertas); reconexiones resueltas solas |
| Coherencia con el backtest | Re-ejecuta el backtest sobre las mismas fechas: ≥ 90 % de las señales coinciden (misma vela y lado) |
| Slippage real | Mediana ≤ 2 veces el supuesto (5 pb) en entradas y stops; si no, sube `slippage_bps` y vuelve a la fase 1 |
| Resultados | Expectativa en R dentro del intervalo esperado del backtest (± 2 errores típicos). La demo no tiene que ser rentable en 6 semanas, sí coherente |
| Kill switch y rearme | Probados al menos una vez con posición abierta |

> El libro de la demo **no** es el real: la liquidez y el slippage difieren.
> Sirve para validar la mecánica, no la rentabilidad.

### Fase 3: real con capital mínimo (8 semanas)
- Capital reducido: `max_capital_usd` en un 10–20 % del capital previsto, o el
  mínimo que permita el tamaño mínimo del contrato.
- Mismos criterios operativos que en demo, y el slippage real dentro de lo
  supuesto.
- **Criterios de parada:**
  - Drawdown > 1,5 veces el máximo del backtest OOS.
  - 2 paradas diarias en una semana.
  - Cualquier posición sin stop.

  Si se da cualquiera de ellos, `kill`, análisis y vuelta a demo.

### Fase 4: escalado
- Sube el capital por escalones (por ejemplo ×2) solo tras 4 semanas sin
  incidencias en cada escalón.
- Revisión mensual: métricas en vivo frente a backtest y funding pagado.

---

## Desarrollo

```bash
python -m pytest -q                   # suite completa (~5 s, sin red)
python -m pytest tests/test_backtest.py -k martingale -q
```

Los tests no usan la red. La API de Kraken está simulada con datos sintéticos
y un broker en memoria.
