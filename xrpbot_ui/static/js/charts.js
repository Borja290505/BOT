/* xrpbot · gráficos (TradingView Lightweight Charts 4.2, licencia Apache-2.0)
 * Colores tomados de los tokens CSS (validados para daltonismo, ver docs/ui/sistema-diseno.md):
 * velas azul HUECA (alcista) / naranja RELLENA (bajista); indicador principal aqua con etiqueta en el eje;
 * niveles ENTRADA/TP/STOP/LIQ con etiqueta propia. Un panel por escala (nunca dos ejes Y).
 * Se mantiene el logo de atribución de TradingView que exige la librería.
 */
"use strict";
(function () {
  const XB = window.XB;
  const charts = (XB.charts = {});
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const LS = { solid: 0, dotted: 1, dashed: 2 };
  const registry = new Map();   // gráfico -> opciones propias (se conservan al cambiar tema/zona)

  function colors() {
    const classic = XB.classicCandles;
    const light = document.documentElement.dataset.theme === "light";
    return {
      bg: css("--surface-1"), text: css("--chart-axis"), grid: css("--chart-grid"), border: css("--border"),
      up: classic ? (light ? "#1baf7a" : "#199e70") : css("--chart-up"),
      down: classic ? (light ? "#e34948" : "#e66767") : css("--chart-down"),
      ind1: css("--chart-ind-1"), line: css("--chart-line"), dd: css("--chart-drawdown"),
      entry: css("--level-entry"), tp: css("--level-tp"), stop: css("--level-stop"), liq: css("--level-liq"),
    };
  }

  function tzOffsetSec(t) {
    if (XB.tz !== "local") return 0;
    const d = new Date(t * 1000);
    const mad = new Date(d.toLocaleString("en-US", { timeZone: "Europe/Madrid" }));
    const utc = new Date(d.toLocaleString("en-US", { timeZone: "UTC" }));
    return (mad - utc) / 1000;
  }

  function baseOptions() {
    const c = colors();
    return {
      autoSize: true,
      layout: { background: { type: "solid", color: c.bg }, textColor: c.text, fontFamily: css("--font-ui"), attributionLogo: true },
      grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
      rightPriceScale: { borderColor: c.border },
      timeScale: { borderColor: c.border, timeVisible: true, secondsVisible: false,
        tickMarkFormatter: (t, type) => {
          const d = new Date((t + tzOffsetSec(t)) * 1000);
          const p = (n) => String(n).padStart(2, "0");
          if (type <= 2) return `${p(d.getUTCDate())}/${p(d.getUTCMonth() + 1)}`;
          return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
        } },
      crosshair: { mode: 0 },
      localization: { locale: "es-ES", timeFormatter: (t) => XB.time(t, true),
        priceFormatter: (p) => XB.num(p, XB.decimals()) },
      handleScale: { axisPressedMouseMove: true }, handleScroll: { vertTouchDrag: false },
    };
  }

  function merged(extra) {
    const base = baseOptions();
    const fmt = extra && extra.priceFormatter;
    const out = Object.assign(base, extra || {});
    if (fmt) out.localization = Object.assign({}, base.localization, { priceFormatter: fmt });
    delete out.priceFormatter;
    if (extra && extra.timeScale) out.timeScale = Object.assign({}, base.timeScale, extra.timeScale);
    return out;
  }
  const byEl = new WeakMap();
  function make(el, extra) {
    if (!window.LightweightCharts || !el) return null;
    const prev = byEl.get(el);
    if (prev) { registry.delete(prev); try { prev.remove(); } catch (e) { /* ya eliminado */ } }
    el.replaceChildren();
    const chart = LightweightCharts.createChart(el, merged(extra));
    registry.set(chart, extra || {});
    byEl.set(el, chart);
    return chart;
  }

  function syncTime(a, b) {
    if (!a || !b) return;
    let busy = false;
    const link = (src, dst) => src.timeScale().subscribeVisibleLogicalRangeChange((r) => {
      if (busy || !r) return; busy = true; dst.timeScale().setVisibleLogicalRange(r); busy = false;
    });
    link(a, b); link(b, a);
  }

  // -------------------------------------------------------------- velas
  charts.candles = function (el, data, opts = {}) {
    const chart = make(el);
    if (!chart) return null;
    const c = colors();
    const series = chart.addCandlestickSeries({
      upColor: "rgba(0,0,0,0)", borderUpColor: c.up, wickUpColor: c.up,
      downColor: c.down, borderDownColor: c.down, wickDownColor: c.down,
      priceFormat: { type: "price", precision: XB.decimals(), minMove: data.tick_size || 0.0001 },
    });
    series.setData(data.candles);
    const ind = {};
    for (const i of data.indicators.filter((x) => x.pane === "price")) {
      const s = chart.addLineSeries({ color: i.color === "ind-1" ? c.ind1 : c.line, lineWidth: 2, lineStyle: LS[i.style] ?? 0,
        priceLineVisible: false, lastValueVisible: true, title: i.name.replace("Donchian", "DC"), crosshairMarkerVisible: false });
      s.setData(i.points);
      ind[i.id] = s;
    }
    const markers = (data.markers || []).map((m) => ({
      time: m.time,
      position: m.kind === "entry" ? (m.side === "long" ? "belowBar" : "aboveBar") : (m.side === "long" ? "aboveBar" : "belowBar"),
      shape: m.kind === "entry" ? (m.side === "long" ? "arrowUp" : "arrowDown") : "circle",
      color: m.kind === "entry" ? css("--text") : css("--text-2"),
      text: opts.markerText === false ? "" : (m.kind === "entry" ? (m.side === "long" ? "L" : "C") : "×"),
    }));
    series.setMarkers(markers);
    let lines = [];
    function setLevels(levels) {
      for (const l of lines) series.removePriceLine(l);
      lines = [];
      const cl = colors();
      const style = { entry: [cl.entry, LS.dashed], tp: [cl.tp, LS.solid], stop: [cl.stop, LS.solid], liq: [cl.liq, LS.dotted] };
      for (const lv of levels || []) {
        if (!XB.isNum(lv.price)) continue;
        const [color, ls] = style[lv.id] || [cl.entry, LS.solid];
        lines.push(series.createPriceLine({ price: lv.price, color, lineWidth: 2, lineStyle: ls, axisLabelVisible: true, title: lv.label }));
      }
    }
    setLevels(data.levels);
    let atrChart = null;
    const atr = data.indicators.find((x) => x.pane === "separate");
    if (opts.atrEl && atr) {
      atrChart = make(opts.atrEl, { timeScale: { visible: false }, priceFormatter: (v) => XB.num(v, 4) });
      const s = atrChart.addLineSeries({ color: c.line, lineWidth: 2, priceLineVisible: false, title: atr.name,
        priceFormat: { type: "price", precision: 5, minMove: 0.00001 } });
      // mismos instantes que las velas (huecos vacíos al principio) para que los índices coincidan
      const byTime = new Map(atr.points.map((pt) => [pt.time, pt.value]));
      s.setData(data.candles.map((cd) => (byTime.has(cd.time) ? { time: cd.time, value: byTime.get(cd.time) } : { time: cd.time })));
      syncTime(chart, atrChart);
    }
    if (data.candles.length > 160) {
      const r = { from: data.candles.length - 160, to: data.candles.length + 3 };
      chart.timeScale().setVisibleLogicalRange(r);
      if (atrChart) atrChart.timeScale().setVisibleLogicalRange(r);
    }
    return (charts.last = {
      chart, series, ind, atrChart,
      setVisible(id, v) { if (ind[id]) ind[id].applyOptions({ visible: v }); },
      setLevels,
      updateLast(price) {
        const last = data.candles[data.candles.length - 1];
        if (!last || !XB.isNum(price)) return;
        const bar = { time: last.time, open: last.open, high: Math.max(last.high, price), low: Math.min(last.low, price), close: price };
        data.candles[data.candles.length - 1] = bar;
        series.update(bar);
      },
    });
  };

  // -------------------------------------------------------------- líneas (capital, drawdown)
  charts.line = function (el, points, { color = "line", area = false, unit = "" } = {}) {
    const chart = make(el, { priceFormatter: (v) => XB.num(v, 2) + unit });
    if (!chart) return null;
    const c = colors();
    const col = c[color] || color;
    const opts = { color: col, lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
      priceFormat: { type: "custom", formatter: (v) => XB.num(v, 2) + unit } };
    const s = area ? chart.addAreaSeries(Object.assign(opts, { lineColor: col, topColor: col + "00", bottomColor: col + "55", invertFilledArea: true }))
                   : chart.addLineSeries(opts);
    s.setData(dedupe(points));
    chart.timeScale().fitContent();
    return { chart, series: s };
  };

  function dedupe(points) {
    const out = [];
    for (const p of points) {
      if (out.length && out[out.length - 1].time >= p.time) { out[out.length - 1] = { time: out[out.length - 1].time, value: p.value }; continue; }
      out.push(p);
    }
    return out;
  }
  charts.dedupe = dedupe;
  charts.sync = syncTime;

  // -------------------------------------------------------------- tabla alternativa
  charts.table = function (host, headers, rows) {
    const h = XB.h;
    XB.fill(host, h("table", { class: "data" },
      h("thead", null, h("tr", null, headers.map((x, i) => h("th", { class: i ? "r" : "" }, x)))),
      h("tbody", null, rows.map((r) => h("tr", null, r.map((v, i) => h("td", { class: i ? "r" : "" }, v)))))));
  };

  // Tema / zona horaria: se re-aplican opciones a todos los gráficos vivos
  function restyle() { for (const [c, extra] of registry) { try { c.applyOptions(merged(extra)); } catch (e) { registry.delete(c); } } }
  document.addEventListener("themechange", () => { restyle(); document.dispatchEvent(new CustomEvent("chartsrestyle")); });
  document.addEventListener("tzchange", restyle);
})();
