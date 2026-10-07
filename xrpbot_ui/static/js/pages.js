/* xrpbot · lógica de cada pantalla. Todo texto externo se inserta con textContent. */
"use strict";
(function () {
  const XB = window.XB;
  const { h, $, $$, fill, num, usd, pct, price } = XB;
  const tip = (key) => h("button", { class: "tip", type: "button", "data-term": key, "aria-label": "Qué es: " + (XB.glossary[key] || [key])[0], "aria-expanded": "false" }, "i");
  const dash = "—";
  const empty = (icon, title, sub) => h("div", { class: "empty" }, h("span", { class: "icon", "aria-hidden": "true" }, icon), title, sub ? h("br") : null, sub ? h("span", { class: "muted", text: sub }) : null);
  const kv = (pairs) => pairs.flatMap(([k, v, cls]) => [h("dt", null, ...(Array.isArray(k) ? k : [k])), h("dd", { class: cls || "" }, v ?? dash)]);
  const levelCls = { info: "neutral", warning: "warning", error: "serious", critical: "critical", debug: "neutral" };
  const levelPill = (lvl) => h("span", { class: `status status-${levelCls[lvl] || "neutral"}`, style: "font-size:12px" }, (lvl || "").toUpperCase());
  const side = (s) => (s === "long" ? "▲ Largo" : s === "short" ? "▼ Corto" : dash);
  const reasonsText = { stop: "Stop", stop_gap: "Stop (hueco)", take_profit: "Take profit", daily_loss_halt: "Parada diaria",
    cierre_manual: "Cierre manual", kill_switch: "Kill switch", end_of_data: "Fin de datos" };
  function meter(el, frac, level, left, right) {
    if (!el) return;
    el.dataset.level = level;
    const fill_ = el.querySelector(".meter-fill");
    fill_.style.width = Math.max(0, Math.min(100, (frac || 0) * 100)) + "%";
    const tr = el.querySelector(".meter-track");
    if (tr) { tr.setAttribute("aria-valuenow", Math.round((frac || 0) * 100)); tr.setAttribute("aria-valuetext", left); }
    el.querySelector(".meter-legend .l").textContent = left;
    el.querySelector(".meter-legend .r").textContent = right || "";
  }
  const lvlFor = (frac) => (frac >= 0.9 ? "critical" : frac >= 0.66 ? "serious" : frac >= 0.4 ? "warning" : "good");

  // ============================================================ RESUMEN
  XB.pages.resumen = {
    init() {
      let range = 30, eq = null;
      const draw = async () => {
        try { eq = eq || (await XB.get("/api/equity")); } catch (e) { fill("#chart-equity", empty("✖", "No se pudo cargar la curva", e.message)); return; }
        if (!eq.points.length) { fill("#chart-equity", empty("∅", "Todavía no hay historial de capital.")); $("#chart-dd").hidden = true; return; }
        const cut = range ? Date.now() / 1000 - range * 86400 : 0;
        const pts = eq.points.filter((p) => p.time >= cut);
        const dd = eq.drawdown.filter((p) => p.time >= cut);
        const a = XB.charts.line($("#chart-equity"), pts.length ? pts : eq.points, { color: "line", unit: " USD" });
        if (dd.length) { const b = XB.charts.line($("#chart-dd"), dd, { color: "dd", area: true, unit: " %" }); XB.charts.sync(a && a.chart, b && b.chart); }
        else $("#chart-dd").hidden = true;
        $("#equity-note").textContent = eq.note || "Arriba: capital (USD). Abajo: drawdown (%) desde el máximo anterior.";
        XB.charts.table($("#equity-table"), ["Fecha", "Capital"], pts.slice(-200).reverse().map((p) => [XB.time(p.time, true), usd(p.value)]));
      };
      for (const b of $$("[data-range]")) b.addEventListener("click", () => {
        range = Number(b.dataset.range);
        for (const x of $$("[data-range]")) x.setAttribute("aria-pressed", String(x === b));
        draw();
      });
      $('[data-table-toggle="equity"]').addEventListener("click", (e) => { const t = $("#equity-table"); t.hidden = !t.hidden; e.target.textContent = t.hidden ? "Ver datos" : "Ocultar datos"; });
      document.addEventListener("chartsrestyle", () => { draw(); });
      draw();
      setInterval(() => { eq = null; draw(); }, 5 * 60 * 1000);
      XB.onState(render);
    },
  };
  function render(st) {
    const s = st.snapshot, a = s.account, bot = s.bot;
    fill("#sum-state", XB.statusPill(bot.effective_state));
    $("#sum-reason").textContent = bot.reason || (bot.effective_state === "unknown" ? s.pending_note || "" : "");
    $("#sum-heartbeat").textContent = bot.heartbeat_at ? "Último latido " + XB.agoText(XB.ago(bot.heartbeat_at)) : "Sin latido publicado";
    const pd = XB.isNum(a.pnl_day_usd) ? a.pnl_day_usd : a.pnl_day_realized_usd;
    const pdEl = $("#sum-pnl-day");
    pdEl.className = "value " + XB.pnlClass(pd); pdEl.textContent = XB.arrow(pd) + usd(pd, true);
    $("#sum-pnl-day-pct").textContent = XB.isNum(a.pnl_day_usd) && a.day_start_equity_usd ? pct(a.pnl_day_usd / a.day_start_equity_usd, 2, true) + " del capital al inicio del día"
      : (XB.isNum(a.pnl_day_realized_usd) ? "Solo realizado (sin equity en vivo)" : "");
    const pt = a.pnl_total_usd, ptEl = $("#sum-pnl-total");
    ptEl.className = "value " + XB.pnlClass(pt); ptEl.textContent = XB.arrow(pt) + usd(pt, true);
    $("#sum-pnl-total-sub").textContent = a.initial_capital_usd ? pct(pt / a.initial_capital_usd, 1, true) + " sobre " + usd(a.initial_capital_usd) : "Realizado acumulado";
    $("#sum-equity").textContent = usd(a.equity_usd);
    // posición
    const p = s.position;
    if (!p) fill("#sum-position", empty("∅", "Sin posición abierta", "El bot abrirá una cuando haya señal y el riesgo lo permita."));
    else fill("#sum-position",
      p.unknown ? h("div", { class: "alert alert-critical" }, h("span", { class: "icon" }, "❄"), h("div", null, h("strong", { text: "Posición DESCONOCIDA para el bot" }), "Revisa la cuenta en Kraken antes de rearmar.")) : null,
      h("p", { class: "value-line num" }, h("strong", { text: `${side(p.side)} ${num(p.size, 0)} XRP @ ${price(p.entry_price)}` })),
      h("dl", { class: "kv" }, ...kv([
        [["P&L latente ", tip("unrealized_pnl")], `${XB.arrow(p.unrealized_pnl_usd)}${usd(p.unrealized_pnl_usd, true)}${XB.isNum(p.r_multiple) ? " (" + XB.r(p.r_multiple) + ")" : ""}`, XB.pnlClass(p.unrealized_pnl_usd)],
        [["Stop ", tip("stop_loss")], XB.isNum(p.stop_price) ? `${price(p.stop_price)}${XB.isNum(p.dist_stop_pct) ? " · a " + pct(p.dist_stop_pct, 2) : ""}` : dash],
        [["Liquidación ", tip("liquidation")], XB.isNum(p.liquidation_price) ? `${price(p.liquidation_price)} · a ${pct(p.dist_liq_pct, 1)}` : dash],
      ])));
    // breakers
    const blocking = s.breakers.filter((b) => b.state === "blocking");
    if (!s.breakers.length) fill("#sum-breakers", h("p", { class: "muted", text: s.pending_note || "Sin datos de circuit breakers." }));
    else fill("#sum-breakers",
      blocking.length ? h("div", { class: "alert alert-warning" }, h("span", { class: "icon" }, "⚠"), h("div", null, h("strong", { text: `${blocking.length} bloqueando nuevas entradas` }),
        h("ul", { class: "reasons" }, blocking.map((b) => h("li", { text: `${b.label}: ${b.value_text} (umbral ${b.threshold_text})` })))))
        : h("p", null, h("span", { class: "status status-good", style: "font-size:12px" }, "✔ OK"), " ", h("strong", { text: "Ninguno bloquea entradas" }), h("span", { class: "muted", text: ` · ${s.breakers.filter((b) => b.state === "ok").length} comprobaciones en orden` })),
      s.risk.entries_blocked && !blocking.length ? h("ul", { class: "reasons small" }, s.risk.block_reasons.map((r) => h("li", { text: r }))) : null);
    // último evento
    const ev = s.last_event;
    fill("#sum-event", ev ? h("div", { class: "row-between" }, h("div", null, levelPill(ev.level), " ", h("span", { text: ev.message })), h("span", { class: "muted small", text: XB.time(ev.ts) })) : h("p", { class: "muted", text: "Sin eventos importantes." }));
    XB.updateControlbar(st);
  }

  // ============================================================ MERCADO
  XB.pages.mercado = {
    async init() {
      let cc = null, data = null;
      const load = async () => {
        try { data = await XB.get("/api/candles?limit=500"); } catch (e) { fill("#chart-price", empty("✖", "No se pudieron cargar las velas", e.message)); return; }
        if (!data.candles.length) { fill("#chart-price", empty("∅", "Sin velas", data.note || "")); $("#chart-atr").hidden = true; return; }
        cc = XB.charts.candles($("#chart-price"), data, { atrEl: $("#chart-atr") });
        if (data.note) $("#mk-note").textContent = data.note;
        // leyenda con conmutadores
        const legend = [];
        const sw = (style) => h("span", { class: "swatch " + (style === "solid" ? "" : style) });
        legend.push(h("span", { class: "key" }, h("span", { class: "cswatch up", "aria-hidden": "true" }), "Alcista (hueca)"));
        legend.push(h("span", { class: "key" }, h("span", { class: "cswatch down", "aria-hidden": "true" }), "Bajista (rellena)"));
        for (const i of data.indicators.filter((x) => x.pane === "price")) {
          const id = "ind-" + i.id;
          legend.push(h("label", { class: "key check-sm", style: "color:var(--chart-ind-1)" },
            h("input", { type: "checkbox", id, checked: true, onchange: (e) => cc && cc.setVisible(i.id, e.target.checked) }), sw(i.style), h("span", { class: "text-2", text: i.name })));
        }
        legend.push(h("span", { class: "key", style: "color:var(--level-stop)" }, sw("solid"), h("span", { class: "text-2" }, "Stop")));
        legend.push(h("span", { class: "key", style: "color:var(--level-tp)" }, sw("solid"), h("span", { class: "text-2" }, "TP")));
        legend.push(h("span", { class: "key", style: "color:var(--level-liq)" }, sw("dotted"), h("span", { class: "text-2" }, "Liquidación")));
        legend.push(h("span", { class: "key" }, "▲▼ entrada · ● salida"));
        fill("#mk-legend", legend);
        XB.charts.table($("#candles-table"), ["Hora", "Apertura", "Máximo", "Mínimo", "Cierre"],
          data.candles.slice(-100).reverse().map((c) => [XB.time(c.time, true), price(c.open), price(c.high), price(c.low), price(c.close)]));
        const lv = data.levels || [];
        fill("#mk-levels", lv.length ? h("dl", { class: "kv" }, ...kv(lv.map((l) => [l.label, price(l.price)]))) : "Sin posición abierta.");
      };
      $('[data-table-toggle="candles"]').addEventListener("click", (e) => { const t = $("#candles-table"); t.hidden = !t.hidden; e.target.textContent = t.hidden ? "Ver datos" : "Ocultar datos"; });
      document.addEventListener("chartsrestyle", load);
      await load();
      let lastHour = Math.floor(Date.now() / 3600000);
      setInterval(() => { const hr = Math.floor(Date.now() / 3600000); if (hr !== lastHour) { lastHour = hr; load(); } }, 30000);
      XB.onState((st) => {
        const m = st.snapshot.market;
        $("#mk-mark").textContent = m ? price(m.mark) : dash;
        $("#mk-last").textContent = m ? price(m.last) : dash;
        $("#mk-spread").textContent = m ? num(m.spread_bps, 1) + " pb" : dash;
        $("#mk-funding").textContent = m && XB.isNum(m.funding_rate_hourly) ? pct(m.funding_rate_hourly, 4, true) + "/h" : dash;
        const fresh = m && XB.ago(m.at) < 30;
        if (cc && fresh) cc.updateLast(Number(m.mark.toFixed(XB.decimals())));
      });
    },
  };

  // ============================================================ POSICIÓN
  XB.pages.posicion = { init() { XB.onState(renderPos); } };
  function renderPos(st) {
    const s = st.snapshot, p = s.position;
    XB.updateControlbar(st);
    fill("#pos-alert");
    if (!p) {
      fill("#pos-body", empty("∅", "Sin posición abierta", "Cuando el bot abra una, verás aquí su detalle, su stop y su take profit."));
      $("#pos-liq-card").hidden = true;
    } else {
      if (p.unknown) fill("#pos-alert", h("div", { class: "alert alert-critical" }, h("span", { class: "icon" }, "❄"), h("div", null, h("strong", { text: "Posición desconocida para el bot" }), "Apareció en la reconciliación. El bot está congelado y ha puesto un stop de emergencia. Revisa la cuenta en Kraken.")));
      const both = XB.isNum(p.dist_liq_pct) && XB.isNum(p.dist_stop_pct);
      const nearLiq = both && p.dist_liq_pct < 2 * p.dist_stop_pct;
      const belowMin = both && p.dist_liq_pct < 3 * p.dist_stop_pct;
      if (nearLiq) fill("#pos-alert", h("div", { class: "alert alert-critical", role: "alert" }, h("span", { class: "icon" }, "⚠"), h("div", null, h("strong", { text: "LIQUIDACIÓN CERCANA" }),
        `La liquidación está a ${pct(p.dist_liq_pct, 2)}, menos del doble de la distancia al stop (${pct(p.dist_stop_pct, 2)}). Si el precio salta el stop, podría liquidarse.`)));
      else if (belowMin) fill("#pos-alert", h("div", { class: "alert alert-warning", role: "status" }, h("span", { class: "icon" }, "⚠"), h("div", null, h("strong", { text: "Liquidación más cerca de lo que exige el bot" }),
        `Está a ${pct(p.dist_liq_pct, 2)}; el bot exige al menos 3× la distancia al stop (${pct(3 * p.dist_stop_pct, 2)}) para abrir posiciones.`)));
      fill("#pos-body", h("dl", { class: "kv pos-grid-kv" }, ...kv([
        ["Lado", side(p.side)],
        ["Entrada", price(p.entry_price)],
        ["Tamaño", `${num(p.size, 0)} XRP`],
        [["Nocional ", tip("notional")], usd(p.notional_usd)],
        [["Mark price ", tip("mark_price")], price(p.mark_price)],
        [["Apalancamiento efectivo ", tip("effective_leverage")], XB.isNum(p.effective_leverage) ? num(p.effective_leverage, 2) + "×" : dash],
        [["P&L latente ", tip("unrealized_pnl")], `${XB.arrow(p.unrealized_pnl_usd)}${usd(p.unrealized_pnl_usd, true)}`, XB.pnlClass(p.unrealized_pnl_usd)],
        [["Múltiplo R ", tip("r_multiple")], XB.r(p.r_multiple)],
        [["Stop ", tip("stop_loss")], XB.isNum(p.stop_price) ? `${price(p.stop_price)}${XB.isNum(p.dist_stop_pct) ? " · a " + pct(p.dist_stop_pct, 2) : ""}` : dash],
        [["Take profit ", tip("take_profit")], XB.isNum(p.tp_price) ? `${price(p.tp_price)}${XB.isNum(p.dist_tp_pct) ? " · a " + pct(p.dist_tp_pct, 2) : ""}` : dash],
        [["Liquidación ", tip("liquidation")], XB.isNum(p.liquidation_price) ? `${price(p.liquidation_price)} · a ${pct(p.dist_liq_pct, 2)}` : dash],
        [["Funding acumulado ", tip("funding")], XB.isNum(p.funding_accrued_usd) ? usd(p.funding_accrued_usd, true) + " (estimado)" : dash],
        ["Abierta", p.opened_at ? `${XB.time(p.opened_at)} · ${XB.agoText(XB.ago(p.opened_at))}` : dash],
      ])));
      const card = $("#pos-liq-card");
      card.hidden = !(XB.isNum(p.dist_stop_pct) || XB.isNum(p.dist_liq_pct));
      if (XB.isNum(p.dist_stop_pct)) meter($("#meter-stop"), Math.min(1, p.dist_stop_pct / 0.05), p.dist_stop_pct < 0.005 ? "serious" : "good",
        `Stop a ${pct(p.dist_stop_pct, 2)} del precio`, "Escala: 0–5 %");
      if (XB.isNum(p.dist_liq_pct)) meter($("#meter-liq"), Math.min(1, p.dist_liq_pct / 0.5), nearLiq ? "critical" : p.dist_liq_pct < 3 * p.dist_stop_pct ? "warning" : "good",
        `Liquidación a ${pct(p.dist_liq_pct, 2)}`, `Mínimo exigido: 3× la distancia al stop (${pct(3 * p.dist_stop_pct, 2)})`);
      const mk = $("#meter-liq .meter-mark");
      if (mk && XB.isNum(p.dist_stop_pct)) mk.style.left = Math.min(100, (3 * p.dist_stop_pct / 0.5) * 100) + "%";
    }
    const orders = s.orders || [];
    fill("#orders-body", orders.length ? h("div", { class: "table-wrap" }, h("table", { class: "data" },
      h("thead", null, h("tr", null, ["Rol", "Tipo", "Lado", "Tamaño", "Precio", "Disparo", "Reduce-only", "ID", "Estado"].map((x, i) => h("th", { class: [3, 4].includes(i) ? "r" : "" }, x)))),
      h("tbody", null, orders.map((o) => h("tr", null,
        h("td", { text: { sl: "Stop", tp: "Take profit", emergency_sl: "Stop de emergencia" }[o.role] || o.role }),
        h("td", { text: { stp: "stop a mercado", post: "post-only", lmt: "limitada" }[o.type] || o.type }),
        h("td", { text: o.side === "sell" ? "venta" : "compra" }), h("td", { class: "r", text: num(o.size, 0) }),
        h("td", { class: "r", text: price(o.price) }), h("td", { text: o.trigger || dash }),
        h("td", { text: o.reduce_only ? "✔ sí" : "✖ NO" }), h("td", { class: "mono small", text: o.cli_ord_id || dash }), h("td", { text: o.status || dash }))))))
      : empty("∅", "Sin órdenes abiertas", p ? "⚠ Hay posición sin órdenes de protección visibles: revisa la reconciliación." : ""));
  }

  // ============================================================ RIESGO
  XB.pages.riesgo = { init() { XB.onState(renderRisk); } };
  function renderRisk(st) {
    const s = st.snapshot, r = s.risk;
    XB.updateControlbar(st);
    fill("#risk-blocked", r.entries_blocked ? h("div", { class: "alert alert-warning" }, h("span", { class: "icon" }, "⚠"),
      h("div", null, h("strong", { text: "Nuevas entradas bloqueadas" }), h("ul", { class: "reasons" }, r.block_reasons.map((x) => h("li", { text: x }))))) : null);
    const frac = r.daily_loss_fraction_of_limit;
    if (XB.isNum(frac)) {
      meter($("#meter-daily"), frac, lvlFor(frac), `${usd(r.daily_loss_used_usd)} de ${usd(r.daily_loss_limit_usd)} (${pct(frac, 0)})`, `Límite ${pct(r.effective.max_daily_loss, 1)} del capital al inicio del día`);
      fill("#daily-badge", frac >= 1 ? XB.statusPill("halted", "límite alcanzado") : frac >= 0.66 ? h("span", { class: "status status-serious", style: "font-size:12px" }, "⚠ CERCA DEL LÍMITE") : null);
    } else meter($("#meter-daily"), 0, "good", "Sin dato de equity en vivo", s.pending_note ? "Pendiente de integración" : "");
    $("#daily-reset").textContent = `Reinicio del día en ${XB.duration((new Date(r.reset_at) - Date.now()) / 1000)} (00:00 UTC = ${new Intl.DateTimeFormat("es-ES", { timeZone: "Europe/Madrid", hour: "2-digit", minute: "2-digit" }).format(new Date(r.reset_at))} Madrid).`;
    const p = s.position, maxLev = r.effective.max_leverage;
    const lev = p && XB.isNum(p.effective_leverage) ? p.effective_leverage : 0;
    meter($("#meter-lev"), lev / maxLev, lev > maxLev ? "critical" : lev / maxLev > 0.8 ? "warning" : "good", `${num(lev, 2)}× de ${num(maxLev, 1)}× (límite del bot)`, p ? "" : "Sin posición");
    $("#acct-lev").textContent = XB.isNum(s.account.account_max_leverage) ? num(s.account.account_max_leverage, 1) + "×" : dash;
    // breakers: tabla (escritorio) + lista (móvil)
    const B = s.breakers;
    const stPill = (b) => ({ ok: h("span", { class: "status status-good", style: "font-size:12px" }, "✔ OK"), blocking: h("span", { class: "status status-warning", style: "font-size:12px" }, "⚠ BLOQUEA"),
      no_data: h("span", { class: "status status-neutral", style: "font-size:12px" }, "? SIN DATO"), disabled: h("span", { class: "status status-neutral", style: "font-size:12px" }, "— DESACTIVADO") }[b.state]);
    const termOf = { spread: "spread", book_depth: "book_depth", atr_ratio: "atr", mark_last: "mark_price", funding: "funding", data_age: null };
    fill("#breakers-body", B.length ? [
      h("div", { class: "table-wrap br-table" }, h("table", { class: "data" },
        h("thead", null, h("tr", null, h("th", null, "Estado"), h("th", null, "Condición"), h("th", { class: "r" }, "Actual"), h("th", { class: "r" }, "Umbral"), h("th", null, "Actualizado"))),
        h("tbody", null, B.map((b) => h("tr", { class: b.state === "no_data" ? "stale" : "" }, h("td", null, stPill(b)), h("td", null, b.label, termOf[b.id] ? tip(termOf[b.id]) : null),
          h("td", { class: "r", text: b.value_text }), h("td", { class: "r", text: (b.comparator === ">=" ? "≥ " : "≤ ") + b.threshold_text }), h("td", { class: "muted small", text: XB.agoText(XB.ago(b.updated_at)) })))))),
      h("div", { class: "br-list" }, B.map((b) => h("div", { class: "br-item" + (b.state === "no_data" ? " stale" : "") },
        h("span", { class: "lbl", text: b.label }), stPill(b), h("span", { class: "vals", text: `Actual ${b.value_text} · umbral ${b.comparator === ">=" ? "≥" : "≤"} ${b.threshold_text}` })))),
    ] : h("p", { class: "muted", text: s.pending_note || "Sin datos." }));
    fill("#risk-params", ...kv([
      ["Riesgo por operación", pct(r.effective.risk_per_trade, 2)],
      ["Pérdida diaria máxima", pct(r.effective.max_daily_loss, 2)],
      ["Apalancamiento máximo del bot", num(r.effective.max_leverage, 2) + "×"],
    ]));
  }

  // ============================================================ HISTORIAL
  XB.pages.historial = {
    init() {
      const form = $("#hist-filters");
      let rows = [], page = 0;
      const PER = 25;
      const query = () => new URLSearchParams([...new FormData(form)].filter(([, v]) => v)).toString();
      const load = async () => {
        const q = query();
        $("#csv-link").href = "/api/trades.csv" + (q ? "?" + q : "");
        try { rows = await XB.get("/api/trades" + (q ? "?" + q : "")); } catch (e) { fill("#hist-body", empty("✖", "Error al cargar", e.message)); return; }
        page = 0; draw();
      };
      const draw = () => {
        const net = rows.reduce((a, t) => a + (t.net_pnl || 0), 0);
        $("#hist-summary").textContent = rows.length ? `${rows.length} operaciones · neto ${usd(net, true)} · ganadoras ${pct(rows.filter((t) => t.net_pnl > 0).length / rows.length, 0)}` : "";
        if (!rows.length) { fill("#hist-body", empty("∅", "No hay operaciones", "Con estos filtros no hay resultados, o el bot aún no ha cerrado ninguna.")); fill("#hist-pager"); return; }
        const slice = rows.slice(page * PER, page * PER + PER);
        const cell = (v, cls) => h("td", { class: "r " + (cls || ""), text: v });
        fill("#hist-body", h("div", { class: "table-wrap" }, h("table", { class: "data" },
          h("thead", null, h("tr", null, ["Apertura", "Lado", "Tamaño", "Entrada", "Salida", "Bruto", "Comisiones", "Funding", "Neto", "R", "Motivo", ""].map((x, i) => h("th", { class: i >= 2 && i <= 9 ? "r" : "" }, x)))),
          h("tbody", null, slice.map((t) => h("tr", null,
            h("td", { text: XB.time(t.opened_at, true) }), h("td", { text: side(t.side) }), cell(num(t.size, 0)), cell(price(t.entry_price)), cell(price(t.exit_price)),
            cell(num(t.gross_pnl, 2, true), t.gross_pnl > 0 ? "pos" : t.gross_pnl < 0 ? "neg" : ""), cell(num(t.fees, 2)), cell(num(t.funding, 2, true)),
            cell(num(t.net_pnl, 2, true), t.net_pnl > 0 ? "pos" : t.net_pnl < 0 ? "neg" : ""), cell(XB.isNum(t.r_multiple) ? num(t.r_multiple, 2, true) : dash),
            h("td", { text: reasonsText[t.exit_reason] || t.exit_reason || dash }), h("td", null, h("a", { href: "/historial/" + encodeURIComponent(t.trade_id) }, "Detalle →"))))))));
        const pages = Math.ceil(rows.length / PER);
        fill("#hist-pager", h("button", { class: "btn btn-sm", type: "button", disabled: page === 0, onclick: () => { page--; draw(); } }, "‹ Anterior"),
          h("span", { class: "muted small", text: `${page * PER + 1}–${Math.min(rows.length, (page + 1) * PER)} de ${rows.length}` }),
          h("button", { class: "btn btn-sm", type: "button", disabled: page >= pages - 1, onclick: () => { page++; draw(); } }, "Siguiente ›"));
      };
      form.addEventListener("change", load);
      form.addEventListener("reset", () => setTimeout(load, 0));
      form.addEventListener("submit", (e) => { e.preventDefault(); load(); });
      document.addEventListener("tzchange", draw);
      load();
    },
  };

  // ============================================================ DETALLE DE OPERACIÓN
  XB.pages.operacion = {
    async init() {
      const id = $("#op-id").textContent.trim();
      let t;
      try { t = await XB.get("/api/trades/" + encodeURIComponent(id)); } catch (e) { fill("#op-kv", empty("✖", "Operación no encontrada", e.message)); return; }
      fill("#op-kv", ...kv([
        ["Lado", side(t.side)], ["Tamaño", num(t.size, 0) + " XRP"], ["Apertura", XB.time(t.opened_at)], ["Cierre", XB.time(t.closed_at)],
        ["Entrada", price(t.entry_price)], ["Salida", price(t.exit_price)], ["Stop inicial", price(t.stop_price)], ["Take profit", price(t.tp_price)],
        ["P&L bruto", usd(t.gross_pnl, true)], ["Comisiones", usd(t.fees)], [["Funding ", tip("funding")], usd(t.funding, true)],
        ["P&L neto", `${XB.arrow(t.net_pnl)}${usd(t.net_pnl, true)}`, XB.pnlClass(t.net_pnl)], [["Múltiplo R ", tip("r_multiple")], XB.r(t.r_multiple)],
        ["Riesgo asumido", usd(t.risk_usd)], ["Motivo de salida", reasonsText[t.exit_reason] || t.exit_reason], ["Señal", t.signal || dash],
      ]));
      fill("#op-timeline", (t.timeline || []).length ? h("ol", { class: "timeline" }, t.timeline.map((x) => h("li", null, h("span", { class: "muted small num", text: XB.time(x.ts, true) }),
        h("div", null, h("div", { class: "what", text: x.what }), h("div", { class: "text-2 small", text: x.detail }))))) : empty("∅", "Sin cronología registrada"));
      try {
        const data = await XB.get("/api/candles?limit=2000");
        const t0 = Date.parse(t.opened_at) / 1000, t1 = Date.parse(t.closed_at) / 1000;
        const i0 = data.candles.findIndex((c) => c.time >= t0 - 3600 * 30);
        if (i0 < 0 || !data.candles.length || data.candles[0].time > t1) { $("#op-chart-note").textContent = "La operación queda fuera de las velas disponibles."; $("#chart-op").hidden = true; return; }
        data.markers = data.markers.filter((m) => m.time >= t0 - 3600 && m.time <= t1 + 3600);
        data.levels = [{ id: "entry", label: "ENTRADA", price: t.entry_price }, { id: "stop", label: "STOP", price: t.stop_price }, { id: "tp", label: "TP", price: t.tp_price }];
        const cc = XB.charts.candles($("#chart-op"), data, {});
        const a = data.candles.findIndex((c) => c.time >= t0), b = data.candles.findIndex((c) => c.time >= t1);
        cc && cc.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, a - 30), to: (b < 0 ? data.candles.length : b) + 15 });
        $("#op-chart-note").textContent = "Niveles iniciales de la operación. ▲▼ entrada · ● salida.";
      } catch (e) { $("#op-chart-note").textContent = "No se pudo cargar el gráfico: " + e.message; }
    },
  };

  // ============================================================ BACKTEST
  XB.pages.backtest = {
    init() {
      const form = $("#bt-form");
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(form);
        const body = Object.fromEntries([...fd].filter(([, v]) => v !== ""));
        body.funding_filter = $("#bt-ff").checked; body.walkforward = $("#bt-wf").checked;
        $("#bt-error").hidden = true; $("#bt-run").disabled = true;
        $("#bt-status").textContent = "⟳ Ejecutando en segundo plano…" + (body.walkforward ? " El walk-forward puede tardar un minuto." : "");
        try {
          const { job_id } = await XB.post("/api/backtest", body);
          let job;
          for (;;) {
            await new Promise((r) => setTimeout(r, 1000));
            job = await XB.get("/api/backtest/" + job_id);
            if (job.status !== "running") break;
            $("#bt-status").textContent = `⟳ Ejecutando… ${Math.round(Date.now() / 1000 - job.started)} s`;
          }
          if (job.status === "error") throw new Error(job.error);
          renderBt(job.result);
          $("#bt-status").textContent = "✔ Terminado";
        } catch (ex) {
          $("#bt-error").textContent = "✖ " + ex.message; $("#bt-error").hidden = false; $("#bt-status").textContent = "";
        } finally { $("#bt-run").disabled = false; }
      });
    },
  };
  function renderBt(r) {
    $("#bt-results").hidden = false;
    $("#bt-data-note").textContent = r.data_note;
    $("#bt-elapsed").textContent = `${r.elapsed_s} s · ${r.data_note}`;
    const m = r.metrics;
    const M = (label, v, term) => h("div", { class: "metric" }, h("div", { class: "k" }, label, term ? tip(term) : null), h("div", { class: "v", text: v }));
    fill("#bt-metrics",
      M("Rentabilidad total", pct(m.rentabilidad_total, 1, true)), M("Drawdown máximo", pct(m.max_drawdown, 1), "drawdown"),
      M("Sharpe", XB.isNum(m.sharpe) ? num(m.sharpe, 2) : dash, "sharpe"), M("Sortino", XB.isNum(m.sortino) ? num(m.sortino, 2) : dash, "sortino"),
      M("Profit factor", XB.isNum(m.profit_factor) ? num(m.profit_factor, 2) : dash, "profit_factor"), M("Win rate", pct(m.win_rate, 1), "win_rate"),
      M("Expectativa / operación", `${XB.isNum(m.expectativa_usd) ? usd(m.expectativa_usd, true) : dash} · ${XB.r(m.expectativa_r)}`, "expectancy"),
      M("Operaciones", num(m.num_operaciones, 0)), M("Comisiones totales", usd(m.comisiones_totales)), M("Funding neto", usd(m.funding_neto, true), "funding"),
      M("Racha perdedora máx.", num(m.racha_perdedora_max, 0)), M("Capital final", usd(m.capital_final)));
    $("#bt-disclaimer").textContent = r.disclaimer;
    const a = XB.charts.line($("#chart-bt-equity"), XB.charts.dedupe(r.equity), { color: "line", unit: " USD" });
    const b = XB.charts.line($("#chart-bt-dd"), XB.charts.dedupe(r.drawdown), { color: "dd", area: true, unit: " %" });
    XB.charts.sync(a && a.chart, b && b.chart);
    fill("#bt-funding", r.funding.available ? h("p", { class: "muted", text: "Funding incluido en estos resultados. Ejecuta de nuevo con y sin «Filtro de funding» para comparar." })
      : h("div", { class: "alert alert-info", "aria-disabled": "true" }, h("span", { class: "icon" }, "⏸"), h("div", null, h("strong", { text: "Sección deshabilitada" }), r.funding.note)));
    const wf = r.walkforward;
    $("#bt-wf-card").hidden = !wf;
    if (wf) {
      const rows = wf.windows.map((w) => [XB.time(w.test_inicio, true).slice(0, 10), XB.time(w.test_fin, true).slice(0, 10), `${w.entry_lookback}/${w.exit_lookback}/${num(w.atr_stop_mult, 1)}`,
        XB.isNum(w.sharpe_train) ? num(w.sharpe_train, 2) : dash, XB.isNum(w.sharpe_test) ? num(w.sharpe_test, 2) : dash, pct(w.rent_test, 1, true), pct(w.dd_test, 1), num(w.ops_test, 0)]);
      const host = h("div");
      XB.charts.table(host, ["Test desde", "Test hasta", "Parámetros (ent/sal/ATR×)", "Sharpe entreno", "Sharpe test", "Rent. test", "DD test", "Ops"], rows);
      const o = wf.oos_metrics || {};
      fill("#bt-wf", h("p", { class: "small text-2", text: `Entrenamiento ${wf.config.train_months} meses · test ${wf.config.test_months} · holdout final de ${wf.config.holdout_months} meses (no usado) desde ${XB.time(wf.holdout_start, true).slice(0, 10)}.` }),
        rows.length ? h("div", { class: "table-wrap" }, host.firstChild) : empty("∅", "Datos insuficientes para ninguna ventana"),
        h("p", { class: "small" }, h("strong", { text: "Fuera de muestra concatenado: " }), `rentabilidad ${pct(o.rentabilidad_total, 1, true)} · drawdown ${pct(o.max_drawdown, 1)} · profit factor ${XB.isNum(o.profit_factor) ? num(o.profit_factor, 2) : dash} · ${num(o.num_operaciones, 0)} operaciones`));
    }
    const host = h("div");
    XB.charts.table(host, ["Régimen", "Operaciones", "P&L neto", "Win rate", "Expectativa R", "Profit factor"],
      (r.regimes || []).map((x) => [x.regimen.replace("_", " "), num(x.operaciones, 0), usd(x.pnl_neto, true), pct(x.win_rate, 0), XB.isNum(x.expectativa_r) ? num(x.expectativa_r, 3, true) : dash, XB.isNum(x.profit_factor) ? num(x.profit_factor, 2) : "∞"]));
    fill("#bt-regimes", (r.regimes || []).length ? h("div", { class: "table-wrap" }, host.firstChild) : empty("∅", "Sin operaciones"));
  }

  // ============================================================ CONFIGURACIÓN
  XB.pages.configuracion = {
    async init() {
      const classic = $("#pref-classic"), local = $("#pref-local");
      classic.checked = XB.classicCandles; local.checked = XB.tz === "local";
      classic.addEventListener("change", () => { XB.setPref("classic", classic.checked ? "1" : "0"); XB.classicCandles = classic.checked; });
      local.addEventListener("change", () => { $("#tz-toggle").click(); });
      await loadCfg();
      XB.onState(() => {});
    },
  };
  const isFrac = (unit) => unit === "fracción";
  const show = (v, unit) => (isFrac(unit) ? pct(v, 2) : num(v, 2) + " " + unit);
  async function loadCfg() {
    let cfg;
    try { cfg = await XB.get("/api/config"); } catch (e) { fill("#cfg-params", empty("✖", "Error", e.message)); return; }
    const canControl = !XB.readOnly && !!XB.state.capabilities.control;
    const rows = cfg.params.map((p) => {
      const input = h("input", { class: "input input-sm num", type: "number", step: "any", "aria-label": "Nuevo valor para " + p.label,
        placeholder: isFrac(p.unit) ? "% p. ej. " + num(p.effective * 50, 2) : (p.dir === "lower" ? "menor que " : "mayor que ") + num(p.effective, 2) });
      const err = h("div", { class: "error small", hidden: true });
      const btn = h("button", { class: "btn btn-sm", type: "button", disabled: !canControl, title: canControl ? "" : "Solo lectura / control no disponible" }, p.dir === "lower" ? "Reducir" : "Endurecer");
      btn.addEventListener("click", async () => {
        err.hidden = true;
        let v = Number(String(input.value).replace(",", "."));
        if (!isFinite(v) || input.value === "") { err.textContent = "Introduce un número"; err.hidden = false; return; }
        if (isFrac(p.unit)) v = v / 100;
        const ok = p.dir === "lower" ? v < p.effective : v > p.effective;
        if (!ok) { err.textContent = p.dir === "lower" ? "Solo se puede reducir. Para aumentarlo: edita config/settings.yaml y reinicia el bot." : "Solo se puede endurecer (subir) desde aquí."; err.hidden = false; return; }
        const r = await XB.confirm({ title: (p.dir === "lower" ? "Reducir " : "Endurecer ") + p.label.toLowerCase(), okLabel: "Aplicar",
          body: [h("p", null, h("strong", { text: show(p.effective, p.unit) }), " → ", h("strong", { text: show(v, p.unit) })),
            h("p", { class: "small" }, "El bot lo recoge en su siguiente ciclo y lo guarda como override que sobrevive a reinicios. Si la posición abierta queda fuera del nuevo límite, el bot NO la cierra: avisa y bloquea nuevas entradas.")] });
        if (!r) return;
        await XB.sendCommand("reduce_param", { name: p.name, value: v });
        input.value = "";
        loadCfg();
      });
      return h("tr", null, h("td", { text: p.label }), h("td", { class: "r", text: show(p.file, p.unit) }),
        h("td", { class: "r", text: XB.isNum(p.override) ? show(p.override, p.unit) : dash }), h("td", { class: "r" }, h("strong", { text: show(p.effective, p.unit) })),
        h("td", null, h("div", { class: "actions-inline" }, input, btn), err));
    });
    fill("#cfg-params", h("div", { class: "table-wrap" }, h("table", { class: "data" },
      h("thead", null, h("tr", null, h("th", null, "Parámetro"), h("th", { class: "r" }, "Fichero"), h("th", { class: "r" }, "Override"), h("th", { class: "r" }, "Efectivo"), h("th", null, "Nuevo valor (solo más prudente)"))),
      h("tbody", null, rows))),
      XB.readOnly ? h("p", { class: "muted small", text: "Solo lectura: no se pueden aplicar reducciones desde esta interfaz." }) : null);
    const s = cfg.strategy;
    fill("#cfg-strategy", ...kv([["Estrategia", s.name || "donchian_atr"], ["Timeframe", s.timeframe], [["Canal de entrada ", tip("donchian")], `${s.entry_lookback} velas`],
      ["Canal de salida", `${s.exit_lookback} velas`], [["ATR ", tip("atr")], `${s.atr_period} velas · stop a ${num(Number(s.atr_stop_mult), 1)}× ATR`],
      [["Take profit ", tip("r_multiple")], `${num(Number(s.take_profit_r), 1)} R`], [["Filtro de funding ", tip("funding")], s.funding_filter ? `activado (|funding| ≤ ${pct(s.funding_max_abs_hourly, 3)}/h)` : "desactivado"],
      [["Tope de capital (PILOTO) ", tip("pilot")], XB.isNum(s.max_capital_usd) ? usd(s.max_capital_usd) : "sin tope"]]));
  }

  // ============================================================ SALUD
  XB.pages.salud = { init() { XB.onState(renderHealth); } };
  function renderHealth(st) {
    const s = st.snapshot, H = s.health, b = s.bot, ui = st.ui;
    fill("#health-pending", !H ? h("div", { class: "alert alert-warning" }, h("span", { class: "icon" }, "⚠"), h("div", null, h("strong", { text: "Sin métricas de salud" }), s.pending_note || "")) : null);
    const okPill = (ok, txt) => h("span", { class: `status status-${ok ? "good" : "critical"}`, style: "font-size:12px" }, ok ? "✔ " : "✖ ", txt);
    if (H) {
      fill("#h-conn", ...kv([
        ["WebSocket", okPill(H.ws.connected, H.ws.connected ? "conectado" : "desconectado")],
        ["Último mensaje WS", XB.agoText(XB.ago(H.ws.last_msg_at))], ["Reconexiones hoy", num(H.ws.reconnects_today, 0)],
        ["Latencia REST (p50 / p95)", `${num(H.rest.latency_p50_ms, 0)} ms / ${num(H.rest.latency_p95_ms, 0)} ms`],
        ["Desfase de reloj", `${num(H.clock_offset_s, 3, true)} s ${Math.abs(H.clock_offset_s) < 1 ? "✔" : "⚠ sincroniza NTP"}`],
      ]));
      const tAge = XB.ago(H.ticker_at), cAge = XB.ago(H.last_candle_at);
      fill("#h-data", ...kv([
        ["Ticker", `${XB.agoText(tAge)} ${tAge > 30 ? "⏱ desfasado" : "✔"}`], ["Última vela cerrada", `${XB.time(H.last_candle_at, true)} (${XB.agoText(cAge)})`],
      ]));
      const rl = H.rate_limit;
      meter($("#meter-rate"), rl.used / rl.capacity, lvlFor(rl.used / rl.capacity), `${num(rl.used, 0)} de ${num(rl.capacity, 0)} puntos`, `ventana de ${rl.window_s} s`);
      const rc = H.last_reconcile;
      const rcMap = { ok: ["good", "✔ Sin discrepancias"], fixed: ["warning", "⚠ Corregida automáticamente"], frozen: ["critical", "❄ Discrepancia: bot congelado"] };
      const [cls, txt] = rcMap[rc.result] || ["neutral", rc.result];
      fill("#h-reconcile", h("p", null, h("span", { class: `status status-${cls}`, style: "font-size:12px" }, txt), " ", h("span", { class: "muted small", text: XB.time(rc.at) + " · " + XB.agoText(XB.ago(rc.at)) })),
        rc.notes && rc.notes.length ? h("ul", { class: "reasons" }, rc.notes.map((n) => h("li", { text: n }))) : null);
    } else { fill("#h-conn"); fill("#h-data"); fill("#h-reconcile", h("p", { class: "muted", text: "—" })); }
    fill("#h-bot", ...kv([["Estado", XB.statusPill(b.effective_state)], ["Último latido", b.heartbeat_at ? XB.agoText(XB.ago(b.heartbeat_at)) : "sin latido publicado"],
      ["Versión", b.version ? `${b.version} (${b.commit})` : dash], ["Arrancado", b.started_at ? `${XB.time(b.started_at)} · ${XB.agoText(XB.ago(b.started_at))}` : dash], ["Estrategia", b.strategy || dash]]));
    fill("#h-ui", ...kv([["Fuente de datos", st.capabilities.source === "demo" ? "DEMOSTRACIÓN (simulada)" : "Bot real (solo lectura de su base de datos)"],
      ["Modo de la interfaz", ui.read_only ? "🔒 Solo lectura" : "Control habilitado (requiere desbloqueo)"], ["Usuario", ui.user],
      ["Último dato recibido", XB.agoText((Date.now() - XB.receivedAt) / 1000)]]));
  }

  // ============================================================ REGISTRO
  XB.pages.registro = {
    init() {
      const tabs = $$(".tab");
      const show = (id) => {
        for (const t of tabs) { const on = t.id === id; t.setAttribute("aria-selected", String(on)); $("#" + t.getAttribute("aria-controls")).hidden = !on; }
        if (id === "t-alerts") loadAlerts(); if (id === "t-audit") loadAudit();
      };
      for (const t of tabs) t.addEventListener("click", () => show(t.id));
      const form = $("#log-filters");
      let timer;
      const loadLog = async () => {
        const q = new URLSearchParams([...new FormData(form)].filter(([, v]) => v)).toString();
        let rows;
        try { rows = await XB.get("/api/events" + (q ? "?" + q : "")); } catch (e) { fill("#log-body", empty("✖", "Error", e.message)); return; }
        fill("#log-body", rows.length ? table(["Hora", "Nivel", "Tipo", "Mensaje"], rows.map((r) => [XB.time(r.ts), levelPill(r.level), r.kind, r.message])) : empty("∅", "Sin entradas con estos filtros"));
      };
      form.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(loadLog, 300); });
      form.addEventListener("submit", (e) => e.preventDefault());
      loadLog();
      setInterval(() => { if (!$("#tab-log").hidden) loadLog(); }, 10000);
      document.addEventListener("tzchange", loadLog);
    },
  };
  function table(headers, rows) {
    return h("div", { class: "table-wrap" }, h("table", { class: "data" }, h("thead", null, h("tr", null, headers.map((x) => h("th", null, x)))),
      h("tbody", null, rows.map((r) => h("tr", null, r.map((v) => h("td", { style: "white-space:normal" }, v)))))));
  }
  async function loadAlerts() {
    let rows;
    try { rows = await XB.get("/api/alerts"); } catch (e) { fill("#alerts-body", empty("✖", "Error", e.message)); return; }
    fill("#alerts-body", rows.length ? table(["Hora", "Nivel", "Canal", "Entregada", "Mensaje"], rows.map((r) => [XB.time(r.ts), levelPill(r.level), r.channel,
      r.delivered === null || r.delivered === undefined ? "?" : r.delivered ? "✔ sí" : "✖ no", r.message])) : empty("∅", "No se ha enviado ninguna alerta"));
  }
  async function loadAudit() {
    let rows;
    try { rows = await XB.get("/api/audit"); } catch (e) { fill("#audit-body", empty("✖", "Error", e.message)); return; }
    fill("#audit-body", rows.length ? table(["Hora", "Usuario", "IP", "Acción", "Detalle", "Resultado"], rows.map((r) => [XB.time(r.ts), r.username, r.ip, r.action, r.detail, r.result])) : empty("∅", "Sin acciones registradas"));
  }
})();
