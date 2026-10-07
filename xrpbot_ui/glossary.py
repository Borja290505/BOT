"""Glosario de las explicaciones ⓘ de la interfaz (nivel principiante).

Fuente única: las plantillas lo consultan por clave. Textos cortos (2-3
frases), sin jerga sin explicar y sin promesas de rentabilidad.
"""
from __future__ import annotations

GLOSSARY: dict[str, tuple[str, str]] = {
    "mark_price": ("Mark price",
        "Precio de referencia que calcula Kraken a partir de varios mercados. Se usa para las "
        "liquidaciones y para disparar el stop del bot, porque una sola operación aislada no lo mueve."),
    "last_price": ("Último precio",
        "Precio de la última operación ejecutada en Kraken. Puede dar saltos bruscos cuando hay "
        "pocas órdenes en el libro."),
    "funding": ("Funding",
        "Pago periódico (cada hora) entre compradores y vendedores del perpetuo para mantener su "
        "precio cerca del contado. Si es positivo, pagan los largos; si es negativo, pagan los cortos."),
    "liquidation": ("Precio de liquidación",
        "Precio al que Kraken cerraría la posición a la fuerza porque el margen ya no cubre la "
        "pérdida. Es una estimación. El bot exige que quede al menos 3 veces más lejos que el stop."),
    "r_multiple": ("Múltiplo R",
        "Resultado expresado en unidades de riesgo. 1R es lo que se pierde si salta el stop (≈1 % "
        "del capital). +2R = ganaste el doble de lo arriesgado; −1R = saltó el stop."),
    "drawdown": ("Drawdown",
        "Caída desde el máximo anterior del capital hasta el punto actual. El drawdown máximo es la "
        "peor caída del periodo: mide cuánto dolor habría que aguantar."),
    "effective_leverage": ("Apalancamiento efectivo",
        "Tamaño de la posición (nocional) dividido entre el capital. 1× = posición igual al capital. "
        "El bot nunca pasa de su límite, aunque la cuenta de Kraken permita más."),
    "account_leverage": ("Apalancamiento de la cuenta",
        "Máximo que Kraken permitiría en este contrato con margen aislado. Es solo informativo: "
        "el límite que de verdad aplica el bot es el suyo, que es menor o igual."),
    "notional": ("Nocional",
        "Valor total de la posición: tamaño × precio. Con 7 XRP a 1,49 USD, el nocional es 10,43 USD."),
    "unrealized_pnl": ("P&L latente",
        "Ganancia o pérdida de la posición abierta si se cerrara ahora al mark price. No es dinero "
        "realizado hasta que la posición se cierra."),
    "atr": ("ATR",
        "Average True Range: movimiento medio de una vela en las últimas 14 horas. Mide la "
        "volatilidad. El stop se coloca a un múltiplo del ATR."),
    "donchian": ("Canal Donchian",
        "Máximo y mínimo de las últimas N velas. Cuando el precio cierra por fuera del canal, la "
        "estrategia lo interpreta como una ruptura y abre una posición."),
    "stop_loss": ("Stop loss",
        "Orden en Kraken que cierra la posición si el precio va en contra hasta ese nivel. Limita "
        "la pérdida, aunque en un hueco de precio puede ejecutarse peor."),
    "take_profit": ("Take profit",
        "Orden en Kraken que cierra la posición con ganancia al llegar al objetivo."),
    "reduce_only": ("Reduce-only",
        "La orden solo puede reducir la posición, nunca abrir una nueva ni darle la vuelta."),
    "oco": ("OCO",
        "«Una cancela la otra»: cuando se ejecuta el stop se cancela el take profit, y al revés."),
    "circuit_breaker": ("Circuit breaker",
        "Condición de mercado anómala (spread alto, datos viejos, volatilidad extrema…) que impide "
        "abrir posiciones NUEVAS. No cierra la posición abierta, que sigue protegida por su stop."),
    "spread": ("Spread",
        "Diferencia entre el mejor precio de venta y el de compra. Si es alto, entrar cuesta más."),
    "book_depth": ("Profundidad del libro",
        "Cantidad de órdenes cerca del precio actual. Con poca profundidad, una orden mueve el "
        "precio y se ejecuta peor."),
    "slippage": ("Slippage",
        "Diferencia entre el precio esperado y el de ejecución real."),
    "daily_loss": ("Pérdida diaria",
        "Pérdida desde las 00:00 UTC, incluyendo la posición abierta. Al llegar al límite el bot "
        "cierra todo y se detiene hasta que lo rearmes a mano."),
    "reconciliation": ("Reconciliación",
        "Comparación entre lo que el bot cree que tiene y lo que Kraken dice que tiene. Si no "
        "cuadra y no sabe arreglarlo con seguridad, el bot se congela y avisa."),
    "profit_factor": ("Profit factor",
        "Ganancias brutas ÷ pérdidas brutas. Por encima de 1 se gana más de lo que se pierde; "
        "por debajo de 1, la estrategia pierde."),
    "win_rate": ("Win rate",
        "Porcentaje de operaciones ganadoras. Por sí solo no dice si la estrategia es buena: "
        "importa también cuánto se gana y se pierde en cada una."),
    "expectancy": ("Expectativa",
        "Resultado medio por operación, en USD o en R, después de comisiones y funding."),
    "sharpe": ("Sharpe",
        "Rentabilidad media dividida entre su variabilidad (anualizada). Más alto = resultados más "
        "estables. Por debajo de 0 = pierde."),
    "sortino": ("Sortino",
        "Como el Sharpe, pero solo penaliza la variabilidad de los días con pérdidas."),
    "walk_forward": ("Walk-forward",
        "Validación por tramos: se eligen parámetros con datos pasados y se prueban en el tramo "
        "siguiente, que no se ha usado para elegirlos. Reduce el riesgo de engañarse con el backtest."),
    "pilot": ("PILOTO",
        "LIVE con un tope de capital pequeño: sirve para validar el bot con dinero real arriesgando poco."),
}


def term(key: str) -> tuple[str, str]:
    return GLOSSARY[key]
