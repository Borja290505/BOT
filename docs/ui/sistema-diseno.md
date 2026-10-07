# Sistema de diseño · interfaz de xrpbot

Referencia visual navegable: `docs/ui/sistema-diseno.html`. Ábrela en el
navegador; incluye selector de tema.

Fuente de verdad en código:
- `xrpbot_ui/static/css/tokens.css`: colores, tipografía, espaciado y radios.
- `xrpbot_ui/static/css/components.css`: componentes y estados.
- `xrpbot_ui/glossary.py`: textos de las explicaciones ⓘ.

## Principios

1. **El modo no se puede confundir.**
   - SIM: insignia azul y el texto «Simulado · sin dinero real».
   - LIVE: franja ámbar con «DINERO REAL» y un borde de 4 px alrededor de toda
     la ventana. Si hay tope de nocional, se añade la etiqueta «PILOTO».
2. **El color nunca va solo.** Todo estado lleva icono y texto, y el P&L lleva
   signo (+/−) y flecha (▲/▼). Funciona en escala de grises y con daltonismo.
3. **El dato viejo se nota.** Todo valor en vivo muestra «hace X s». Cuando
   supera su umbral se atenúa (opacidad 0,55) y aparece el aviso «⏱ dato
   desfasado».
4. **Calma, no estímulo.** Sin confeti, rachas, sonidos de victoria ni mensajes
   motivacionales. Las pérdidas y las ganancias tienen el mismo peso visual.
5. **Seguridad visible.**
   - Solo lectura por defecto: los controles aparecen desactivados.
   - El candado de control dice cuánto falta para volver a bloquearse.
   - Las acciones peligrosas usan botones de peligro y diálogos de confirmación.

## Color

Por defecto se usa el tema oscuro; el claro se activa con
`<html data-theme="light">` y la elección se recuerda en el navegador.

### Superficies y texto

Contraste WCAG medido (todo ≥ 4,5:1 sobre las tres superficies):

| Token | Oscuro | Claro | Uso |
|---|---|---|---|
| `--bg` | `#121211` | `#f5f5f3` | Fondo de la página |
| `--surface-1` | `#1a1a19` | `#fcfcfb` | Tarjetas y fondo de gráficos |
| `--surface-2` | `#232321` | `#f0efec` | Cabeceras de tabla y campos |
| `--border` | `#383835` | `#dcdbd5` | Bordes |
| `--text` | `#f2f1ec` (15,4:1) | `#0b0b0b` (19,2:1) | Texto principal |
| `--text-2` | `#c3c2b7` (9,7:1) | `#52514e` (7,7:1) | Etiquetas |
| `--text-muted` | `#9a9990` (6,1:1) | `#66655f` (5,7:1) | Notas y «hace X s» |

### Estado

Colores reservados: solo significan estado y siempre van con icono y texto.
Cada uno tiene una variante para texto que cumple ≥ 4,5:1.

| Rol | Color | Texto (oscuro / claro) | Se usa en |
|---|---|---|---|
| good | `#0ca30c` | `#4cc94c` / `#006300` | ACTIVO, breaker OK, P&L positivo |
| warning | `#fab219` | `#fab219` / `#7a5200` | RECONECTANDO, breaker que bloquea, dato desfasado, franja LIVE |
| serious | `#ec835a` | `#f0956f` / `#9c4119` | DETENIDO por límite diario, cerca del límite |
| critical | `#d03b3b` | `#f07a7a` / `#b02a2a` | CONGELADO, CAÍDO, liquidación cercana, P&L negativo, kill switch |
| info | `#3987e5` / `#2a78d6` | `#6da7ec` / `#1c5cab` | SIM, avisos informativos, foco |

Mapa de estados del bot:

| Estado | Icono | Rol |
|---|---|---|
| ACTIVO | ● | good |
| PAUSADO | ⏸ | neutral |
| DETENIDO · límite diario | ⛔ | serious |
| CONGELADO · discrepancia | ❄ | critical |
| CAÍDO · sin latido hace X s | ✖ | critical |
| RECONECTANDO | ⟳ | warning |
| ARRANCANDO | ◌ | neutral |

### Gráficos

Validados con `validate_palette.js`, que comprueba la separación para
daltonismo (protanopía/deuteranopía, Machado 2009), la separación con visión
normal y el contraste. Resultados medidos:

| Combinación | Modo | ΔE daltonismo (objetivo ≥ 8) | ΔE visión normal (mín. 15) | Resultado |
|---|---|---|---|---|
| Verde/rojo clásico `#1baf7a`/`#e34948` | claro | 6,9 | 31,3 | ⚠ solo con codificación secundaria |
| Verde/rojo clásico `#199e70`/`#e66767` | oscuro | 6,5 | 27,5 | ⚠ solo con codificación secundaria |
| **Azul/naranja** `#2a78d6`/`#eb6834` | claro | 24,7 | 33,6 | ✅ |
| **Azul/naranja** `#3987e5`/`#d95926` | oscuro | 26,8 | 31,8 | ✅ |
| Azul + naranja + violeta, todos los pares | oscuro | 1,9 | 9,8 | ❌ descartado |
| **Azul + naranja + aqua**, todos los pares | claro / oscuro | 9,2 / 9,4 | 24,0 / 20,9 | ✅ |

Decisiones:
- **Velas:** alcista en azul y **hueca**, bajista en naranja y **rellena**. El
  relleno es la segunda codificación, así que se distinguen incluso en blanco y
  negro. Habrá una opción «verde/rojo clásico» en ajustes, siempre con
  hueca/rellena, porque por sí solo ese par queda en la banda de aviso.
- **Indicador principal** (canal de entrada): aqua y discontinuo. En el tema
  claro su contraste es 2,7:1, así que lleva **siempre la etiqueta de valor en
  el eje**. Los indicadores secundarios usan el mismo color con otro trazo
  (punteado) y leyenda. Como máximo 3 colores categóricos en el gráfico de
  velas; si hay más indicadores, van en paneles aparte.
- **Niveles de la posición:** líneas horizontales, cada una con etiqueta propia
  en el eje.
  - ENTRADA: neutra y discontinua.
  - TP: good.
  - STOP: serious.
  - LIQ: critical y punteada, con texto blanco (4,7:1).
- **Paneles separados:** nunca dos escalas en un mismo gráfico. ATR,
  drawdown y funding van en paneles propios bajo el principal.
- **Curva de capital:** una sola serie en azul; el drawdown, en un panel
  inferior en naranja.
- **Interacción:** cruz y tooltip con fecha UTC/Madrid, OHLC y valores de los
  indicadores. Cada gráfico tiene una alternativa en tabla («Ver datos»).

## Tipografía

- **Fuentes:** las del sistema (`system-ui`, Segoe UI en Windows, Roboto en
  Android). Sin descargas, funciona sin conexión.
- **Monoespaciada:** `ui-monospace` / Cascadia Mono / Consolas, para IDs de
  órdenes, precios en tablas y logs.
- **Cifras:** siempre `font-variant-numeric: tabular-nums`, para que las columnas
  no bailen al actualizarse.
- **Escala:** 12 · 14 (base) · 16 · 20 · 24 · 32 (cifra principal).
- **Pesos:** 400 / 500 / 650.

## Espaciado y forma

- **Espaciado:** base de 4 px (4 · 8 · 12 · 16 · 24 · 32 · 48).
- **Radios:** 6 px en controles y 10 px en tarjetas.
- **Objetivo táctil mínimo:** 44 px. Los ⓘ amplían su área táctil sin crecer
  visualmente.
- **Puntos de corte:** escritorio con barra lateral (> 760 px); móvil con barra
  inferior (Resumen · Posición · Riesgo · Más) y barra de control fija encima.

## Componentes

| Componente | Variantes / estados |
|---|---|
| Cabecera de modo | SIM · LIVE · LIVE + PILOTO; en móvil se oculta la hora |
| Insignia de estado del bot | los 7 estados de la tabla anterior |
| Cifra principal (stat) | normal · P&L positivo/negativo con signo y flecha · desfasada · cargando |
| Medidor | good/warning/serious/critical, con marca de umbral y leyenda numérica |
| Tabla de datos | cabecera fija, números a la derecha, scroll horizontal en móvil, fila «sin dato» |
| Lista clave-valor | Detalle de la posición y salud del sistema |
| Aviso (banner) | info · warning · serious · critical |
| ⓘ Glosario | Botón accesible (`aria-expanded`), se abre al tocar o con el teclado; textos en `glossary.py` |
| Botones | primary · neutro · peligro con contorno (cerrar posición) · peligro (kill switch) · desactivado (solo lectura) |
| Candado de control | bloqueado (por defecto) · desbloqueado con cuenta atrás (5 min) |
| Campo de formulario | normal · con error (p. ej. «solo se puede reducir») · ayuda |
| Diálogo de confirmación | 1 paso (pausar/reanudar) · con motivo (rearmar) · escribir palabra (CERRAR) · 2 pasos + palabra (KILL); franja «DINERO REAL» en LIVE |
| Estados de datos | cargando (esqueleto) · vacío · desfasado · sin conexión · bot caído (capa encima con los últimos datos atenuados) · reconectando |

## Formatos (locale es-ES)

- **Precios de XRP:** con los decimales del tick (0,0001 → 4 decimales): `1,4851`.
- **Importes:** `14,60 USD` (2 decimales; 4 si el valor es menor que 0,01).
- **Signo:** el negativo usa el carácter «−» (U+2212). El P&L lleva signo explícito: `+0,04` / `−0,12`.
- **Porcentajes:** `−0,8 %` (con espacio, como en la norma española). Puntos básicos: `3,1 pb`.
- **Fechas:**
  - UTC por defecto: `03/10/2026 16:42:05 UTC`.
  - Con el ajuste de hora local, Europe/Madrid: `18:42 Madrid`. La cabecera muestra ambas.
  - Relativas para la antigüedad: `hace 3 s`, `hace 2 min`.

## Accesibilidad

- **Contraste de texto:** ≥ 4,5:1 en ambos temas (medido).
- **Foco:** visible en todos los controles (`:focus-visible`).
- **Lectores de pantalla:** cada medidor lleva `role="meter"` con sus valores
  `aria-*`, y los diálogos usan `<dialog>` nativo.
- **Movimiento reducido:** con `prefers-reduced-motion` se desactivan
  animaciones y esqueletos animados.
- **Alto contraste de Windows** (`forced-colors`): los bordes sustituyen al color.
