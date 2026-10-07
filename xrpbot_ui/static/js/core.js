/* xrpbot · núcleo de la interfaz
 * - Estado en tiempo real por SSE (/api/stream) con respaldo por sondeo (/api/state).
 * - Formatos es-ES, antigüedad de los datos, cabecera de modo/estado, glosario ⓘ.
 * - Candado de control, diálogos de confirmación y envío de comandos.
 * Sin dependencias. Todo el texto que viene del bot se inserta con textContent (nunca HTML).
 */
"use strict";
(function () {
  const XB = (window.XB = {});
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  XB.$ = $; XB.$$ = $$;

  const body = document.body;
  XB.page = body.dataset.page;
  XB.readOnly = body.dataset.readOnly === "true";
  XB.source = body.dataset.source;
  XB.csrf = $('meta[name="csrf-token"]').content;
  XB.glossary = JSON.parse($("#glossary-data").textContent);
  XB.reducible = JSON.parse($("#reducible-data").textContent);
  XB.state = JSON.parse($("#initial-state").textContent);
  XB.receivedAt = Date.now();
  const listeners = [];
  XB.pages = {};

  // ---------------------------------------------------------------- preferencias
  function pref(key, def) { try { return localStorage.getItem("xrpbot-" + key) ?? def; } catch (e) { return def; } }
  function setPref(key, v) { try { localStorage.setItem("xrpbot-" + key, v); } catch (e) { /* sin almacenamiento */ } }
  XB.pref = pref; XB.setPref = setPref;
  XB.tz = pref("tz", "utc");                     // "utc" | "local" (Europe/Madrid)
  XB.classicCandles = pref("classic", "0") === "1";

  // ---------------------------------------------------------------- DOM
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (k === "dataset") Object.assign(el.dataset, v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }
  XB.h = h;
  XB.fill = (el, ...nodes) => { if (typeof el === "string") el = $(el); if (el) el.replaceChildren(...nodes.flat().filter(Boolean)); return el; };

  // ---------------------------------------------------------------- formatos
  const MINUS = "−";
  const nfCache = {};
  function nf(min, max) {
    const k = min + ":" + max;
    return nfCache[k] || (nfCache[k] = new Intl.NumberFormat("es-ES", { minimumFractionDigits: min, maximumFractionDigits: max, useGrouping: true }));
  }
  const isNum = (v) => typeof v === "number" && isFinite(v);
  XB.isNum = isNum;
  function num(v, dec = 2, signed = false) {
    if (!isNum(v)) return "—";
    let s = nf(dec, dec).format(Math.abs(v));
    if (v < 0 && Number(s.replace(/\./g, "").replace(",", ".")) !== 0) s = MINUS + s;
    else if (signed && v > 0) s = "+" + s;
    return s;
  }
  XB.num = num;
  XB.decimals = () => Math.max(0, Math.round(-Math.log10(XB.state?.snapshot?.tick_size || 0.0001)));
  XB.price = (v) => num(v, XB.decimals());
  XB.usd = (v, signed = false) => {
    if (!isNum(v)) return "—";
    const dec = Math.abs(v) > 0 && Math.abs(v) < 0.01 ? 4 : 2;
    return num(v, dec, signed) + " USD";
  };
  XB.pct = (frac, dec = 1, signed = false) => (isNum(frac) ? num(frac * 100, dec, signed) + " %" : "—");
  XB.r = (v) => (isNum(v) ? num(v, 2, true) + " R" : "—");
  XB.arrow = (v) => (!isNum(v) ? "" : v > 0 ? "▲ " : v < 0 ? "▼ " : "");
  XB.pnlClass = (v) => (!isNum(v) ? "" : v > 0 ? "pnl-pos" : v < 0 ? "pnl-neg" : "pnl-zero");
  const fmtUTC = new Intl.DateTimeFormat("es-ES", { timeZone: "UTC", day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
  const fmtMAD = new Intl.DateTimeFormat("es-ES", { timeZone: "Europe/Madrid", day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
  XB.time = (iso, short = false) => {
    if (!iso) return "—";
    const d = typeof iso === "number" ? new Date(iso * 1000) : new Date(iso);
    if (isNaN(d)) return "—";
    let s = (XB.tz === "local" ? fmtMAD : fmtUTC).format(d).replace(",", "");
    if (short) s = s.slice(0, 16);
    return s + (XB.tz === "local" ? " Madrid" : " UTC");
  };
  XB.ago = (iso) => {
    if (!iso) return null;
    return Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  };
  XB.agoText = (sec) => {
    if (sec === null || sec === undefined) return "—";
    if (sec < 60) return `hace ${Math.round(sec)} s`;
    if (sec < 3600) return `hace ${Math.round(sec / 60)} min`;
    if (sec < 86400) return `hace ${Math.round(sec / 3600)} h`;
    return `hace ${Math.round(sec / 86400)} d`;
  };
  XB.duration = (sec) => {
    sec = Math.max(0, Math.round(sec));
    const hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60);
    return hh ? `${hh} h ${mm} min` : `${mm} min`;
  };

  // ---------------------------------------------------------------- estado del bot
  XB.STATES = {
    active: { cls: "good", icon: "●", txt: "ACTIVO" },
    paused: { cls: "neutral", icon: "⏸", txt: "PAUSADO" },
    halted: { cls: "serious", icon: "⛔", txt: "DETENIDO" },
    frozen: { cls: "critical", icon: "❄", txt: "CONGELADO" },
    reconnecting: { cls: "warning", icon: "⟳", txt: "RECONECTANDO" },
    down: { cls: "critical", icon: "✖", txt: "CAÍDO" },
    starting: { cls: "neutral", icon: "◌", txt: "ARRANCANDO" },
    unknown: { cls: "neutral", icon: "◌", txt: "SIN ESTADO EN VIVO" },
  };
  XB.statusPill = (state, extra) => {
    const s = XB.STATES[state] || XB.STATES.unknown;
    return h("span", { class: `status status-${s.cls}` }, h("span", { class: "icon", "aria-hidden": "true" }, s.icon),
      h("span", { class: "txt" }, s.txt + (extra ? " · " + extra : "")));
  };
  XB.botState = () => XB.state.snapshot.bot.effective_state;
  XB.botAlive = () => !["down", "unknown"].includes(XB.botState());

  function renderHeader(st) {
    const snap = st.snapshot;
    const live = snap.mode === "live";
    $("#app").dataset.mode = snap.mode;
    document.documentElement.dataset.mode = snap.mode;
    $("#mode-badge").className = "badge " + (live ? "badge-live" : "badge-sim");
    $("#mode-badge").textContent = live ? "▲ LIVE" : "◆ SIM";
    $("#mode-text").textContent = live ? "DINERO REAL" : "Simulado · sin dinero real";
    const pilot = $("#pilot-badge");
    pilot.hidden = !(live && snap.pilot && snap.pilot.active);
    if (!pilot.hidden && isNum(snap.pilot.max_capital_usd)) pilot.textContent = `PILOTO · tope ${num(snap.pilot.max_capital_usd, 0)} USD`;
    const state = snap.bot.effective_state;
    let extra = "";
    if (state === "down") extra = "sin latido " + XB.agoText(XB.ago(snap.bot.heartbeat_at));
    else if (state === "halted" && /diari/i.test(snap.bot.reason || "")) extra = "límite diario";
    else if (state === "halted" && /kill/i.test(snap.bot.reason || "")) extra = "kill switch";
    else if (state === "frozen") extra = "discrepancia";
    const pill = XB.statusPill(state, extra);
    pill.id = "bot-status"; pill.setAttribute("role", "status"); pill.setAttribute("aria-live", "polite");
    $("#bot-status").replaceWith(pill);
    document.body.classList.toggle("bot-down", state === "down");
  }

  // ---------------------------------------------------------------- reloj y antigüedad
  const AGE_GROUPS = {
    market: { at: (s) => s.snapshot.market && s.snapshot.market.at, max: 30 },
    account: { at: (s) => s.snapshot.account && s.snapshot.account.equity_at, max: 30 },
  };
  function tick() {
    const now = new Date();
    const utc = now.toISOString().slice(11, 19);
    const mad = new Intl.DateTimeFormat("es-ES", { timeZone: "Europe/Madrid", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(now);
    const clock = $("#clock");
    if (clock) clock.textContent = `${utc} UTC · ${mad} Madrid`;
    for (const [g, def] of Object.entries(AGE_GROUPS)) {
      const at = def.at(XB.state);
      const age = XB.ago(at);
      const stale = at ? age > def.max : true;
      for (const el of $$(`[data-age-of="${g}"]`)) {
        el.textContent = at ? (stale ? "⏱ " : "") + XB.agoText(age) + (stale ? " · dato desfasado" : "") : "sin dato";
        el.classList.toggle("is-stale", stale);
      }
      for (const el of $$(`[data-stale-group="${g}"]`)) el.classList.toggle("stale", stale && !!at);
    }
    // Sin noticias de la interfaz
    const silent = (Date.now() - XB.receivedAt) / 1000;
    if (silent > 12) banner(`Sin datos de la interfaz desde ${XB.agoText(silent)}. Los valores mostrados son los últimos conocidos.`, "critical");
    // Bot caído
    if (XB.botState() === "down") {
      banner(`✖ BOT CAÍDO: sin latido ${XB.agoText(XB.ago(XB.state.snapshot.bot.heartbeat_at))}. Los datos atenuados son los últimos conocidos y los controles están desactivados. Comprueba el proceso del bot en su máquina.`, "critical", "bot");
    } else if (bannerKind === "bot") banner(null);
    // Cuenta atrás del modo control
    const ui = XB.state.ui;
    if (ui.control_seconds_left > 0) {
      ui.control_seconds_left = Math.max(0, ui.control_seconds_left - 1);
      if (ui.control_seconds_left === 0) renderLock();
    }
    renderLockText();
  }

  let bannerKind = null;
  function banner(text, level = "warning", kind = "conn") {
    const el = $("#conn-banner");
    if (!el) return;
    if (!text) { if (!kind || bannerKind === kind || kind === "conn") { el.hidden = true; bannerKind = null; } return; }
    if (bannerKind === "bot" && kind === "conn" && level !== "critical") return;
    el.textContent = text;
    el.className = "conn-banner " + (level === "critical" ? "critical" : "");
    el.hidden = false;
    bannerKind = kind;
  }
  XB.banner = banner;

  // ---------------------------------------------------------------- tiempo real
  function update(data) {
    XB.state = data;
    XB.receivedAt = Date.now();
    if (bannerKind === "conn") banner(null);
    renderHeader(data);
    renderLock();
    for (const fn of listeners) { try { fn(data); } catch (e) { console.error(e); } }
  }
  XB.onState = (fn) => { listeners.push(fn); try { fn(XB.state); } catch (e) { console.error(e); } };
  XB.refresh = async () => {
    const r = await fetch("/api/state", { credentials: "same-origin" });
    if (r.status === 401) { location.href = "/login"; return; }
    if (r.ok) update(await r.json());
  };

  let es = null, failures = 0, pollTimer = null;
  function connect() {
    if (!("EventSource" in window)) return startPolling();
    es = new EventSource("/api/stream");
    es.addEventListener("state", (e) => { failures = 0; stopPolling(); update(JSON.parse(e.data)); });
    es.addEventListener("session", () => { location.href = "/login"; });
    es.onerror = () => {
      failures += 1;
      banner(`⟳ Sin conexión en tiempo real con la interfaz. Reintentando… (${failures})`);
      if (failures >= 3) { es.close(); es = null; startPolling(); setTimeout(connect, 30000); }
    };
  }
  function startPolling() {
    if (pollTimer) return;
    banner("⟳ Conexión en tiempo real no disponible: actualizando cada 5 s.");
    pollTimer = setInterval(() => XB.refresh().catch(() => banner("✖ Sin conexión con la interfaz. Reintentando…", "critical")), 5000);
  }
  function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

  // ---------------------------------------------------------------- glosario ⓘ
  let tipOpen = null;
  function closeTip() {
    const pop = $("#tip-pop");
    if (tipOpen) tipOpen.setAttribute("aria-expanded", "false");
    tipOpen = null; pop.hidden = true;
  }
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".tip[data-term]");
    if (!btn) { if (tipOpen && !e.target.closest("#tip-pop")) closeTip(); return; }
    e.preventDefault();
    if (tipOpen === btn) return closeTip();
    const [title, text] = XB.glossary[btn.dataset.term] || ["", ""];
    const pop = $("#tip-pop");
    XB.fill(pop, h("strong", { text: title }), " — ", text);
    pop.hidden = false;
    const r = btn.getBoundingClientRect();
    const w = Math.min(300, window.innerWidth - 16);
    pop.style.maxWidth = w + "px";
    pop.style.left = Math.max(8, Math.min(window.scrollX + r.left - 20, window.scrollX + window.innerWidth - w - 8)) + "px";
    pop.style.top = window.scrollY + r.bottom + 8 + "px";
    btn.setAttribute("aria-expanded", "true");
    tipOpen = btn;
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeTip(); });

  // ---------------------------------------------------------------- diálogos
  for (const d of $$("dialog")) {
    d.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) d.close("cancel"); });
  }
  XB.confirm = function ({ title, body, okLabel, okClass = "btn-primary", word = null, steps = null }) {
    return new Promise((resolve) => {
      const dlg = $("#confirm-dialog"), form = $("#confirm-form");
      $("#confirm-title").textContent = title;
      XB.fill("#confirm-body", ...(Array.isArray(body) ? body : [body]));
      const ok = $("#confirm-ok");
      ok.textContent = okLabel; ok.className = "btn " + okClass;
      const st = $("#confirm-steps");
      st.hidden = !steps;
      if (steps) XB.fill(st, ...steps.labels.map((l, i) => h("span", { class: i === steps.current ? "on" : "" }, `${i + 1} · ${l}`)));
      const wf = $("#confirm-word-field"), wi = $("#confirm-word");
      wf.hidden = !word; wi.value = "";
      if (word) { $("#confirm-word-label").textContent = word; ok.disabled = true; wi.oninput = () => { ok.disabled = wi.value.trim() !== word; }; }
      else { ok.disabled = false; wi.oninput = null; }
      const onSubmit = (e) => { e.preventDefault(); if (ok.disabled) return; finish({ word: word ? wi.value.trim() : null }); };
      // El evento "close" del paso anterior llega de forma asíncrona: si el diálogo ya se ha
      // reabierto para el paso siguiente, no hay que cerrarlo.
      const onClose = () => { if (!dlg.open) finish(null); };
      function finish(v) {
        form.removeEventListener("submit", onSubmit); dlg.removeEventListener("close", onClose);
        if (dlg.open) dlg.close();
        resolve(v);
      }
      form.addEventListener("submit", onSubmit);
      dlg.addEventListener("close", onClose);
      dlg.showModal();
      (word ? wi : ok).focus();
    });
  };

  // ---------------------------------------------------------------- feedback
  XB.feedback = function (level, title, text) {
    const icons = { info: "ℹ", good: "✔", warning: "⚠", critical: "✖", serious: "⛔" };
    const cls = level === "good" ? "info" : level;
    const box = h("div", { class: `alert alert-${cls}`, role: level === "critical" ? "alert" : "status" },
      h("span", { class: "icon", "aria-hidden": "true" }, icons[level] || "ℹ"),
      h("div", null, h("strong", { text: title }), text || ""),
      h("button", { class: "btn btn-ghost btn-sm", type: "button", "aria-label": "Cerrar aviso", onclick: () => box.remove() }, "×"));
    const host = $("#cmd-feedback");
    host.prepend(box);
    while (host.children.length > 3) host.lastChild.remove();
    return box;
  };

  // ---------------------------------------------------------------- red
  XB.post = async function (url, data) {
    const r = await fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": XB.csrf },
      body: JSON.stringify(data || {}),
    });
    let payload = null;
    try { payload = await r.json(); } catch (e) { payload = null; }
    if (r.status === 401) { location.href = "/login"; throw new Error("Sesión caducada"); }
    if (!r.ok) throw new Error((payload && payload.error) || `Error ${r.status}`);
    return payload;
  };
  XB.get = async function (url) {
    const r = await fetch(url, { credentials: "same-origin" });
    if (r.status === 401) { location.href = "/login"; throw new Error("Sesión caducada"); }
    let payload = null;
    try { payload = await r.json(); } catch (e) { payload = null; }
    if (!r.ok) throw new Error((payload && payload.error) || `Error ${r.status}`);
    return payload;
  };
  function uuid() {
    if (crypto.randomUUID) return crypto.randomUUID();
    const b = crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
    const x = [...b].map((v) => v.toString(16).padStart(2, "0")).join("");
    return `${x.slice(0, 8)}-${x.slice(8, 12)}-${x.slice(12, 16)}-${x.slice(16, 20)}-${x.slice(20)}`;
  }

  // ---------------------------------------------------------------- candado de control
  XB.controlActive = () => !XB.readOnly && XB.state.ui.control_seconds_left > 0;
  function renderLockText() {
    const b = $("#lock-btn");
    if (!b) return;
    const s = XB.state.ui.control_seconds_left;
    if (s > 0) {
      b.dataset.unlocked = "true";
      b.textContent = `🔓 Control · se bloquea en ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
      b.title = "Pulsa para volver a bloquear ahora";
    } else {
      b.dataset.unlocked = "false";
      b.textContent = "🔒 Desbloquear control";
      b.title = "Solo lectura. Pulsa para desbloquear el control (pide tu contraseña)";
    }
  }
  function renderLock() { renderLockText(); XB.updateControlbar && XB.updateControlbar(XB.state); }
  XB.unlock = function () {
    return new Promise((resolve) => {
      const dlg = $("#unlock-dialog"), form = $("#unlock-form"), pw = $("#unlock-pw"), err = $("#unlock-error");
      pw.value = ""; err.hidden = true;
      const onSubmit = async (e) => {
        e.preventDefault();
        try {
          const r = await XB.post("/api/control/unlock", { password: pw.value });
          XB.state.ui.control_seconds_left = r.control_seconds_left;
          pw.value = "";
          done(true);
        } catch (ex) { err.textContent = ex.message; err.hidden = false; pw.select(); }
      };
      const onClose = () => { if (!dlg.open) done(false); };
      function done(v) {
        form.removeEventListener("submit", onSubmit); dlg.removeEventListener("close", onClose);
        if (dlg.open) dlg.close();
        renderLock(); resolve(v);
      }
      form.addEventListener("submit", onSubmit);
      dlg.addEventListener("close", onClose);
      dlg.showModal(); pw.focus();
    });
  };
  const lockBtn = $("#lock-btn");
  if (lockBtn) lockBtn.addEventListener("click", async () => {
    if (XB.controlActive()) {
      await XB.post("/api/control/lock").catch(() => {});
      XB.state.ui.control_seconds_left = 0; renderLock();
    } else {
      await XB.unlock();
    }
  });

  // ---------------------------------------------------------------- comandos
  const CMD_LABEL = { pause: "Pausar entradas", resume: "Reanudar", rearm: "Rearmar", close_position: "Cerrar posición",
    kill: "KILL SWITCH", reduce_param: "Reducir parámetro" };
  XB.sendCommand = async function (type, params, confirmWord) {
    if (XB.readOnly) return XB.feedback("warning", "Solo lectura", "La interfaz no puede enviar comandos.");
    if (!XB.controlActive()) { const ok = await XB.unlock(); if (!ok) return; }
    const id = uuid();
    let cmd;
    try {
      cmd = await XB.post("/api/commands", { id, type, params: params || {}, confirm_word: confirmWord || null });
    } catch (ex) {
      return XB.feedback("critical", `${CMD_LABEL[type]}: no enviado`, ex.message);
    }
    const box = XB.feedback("info", `${CMD_LABEL[type]}: enviado`, "Esperando a que el bot lo valide y lo ejecute…");
    const t0 = Date.now();
    while (Date.now() - t0 < 75000) {
      await new Promise((r) => setTimeout(r, 1000));
      try { cmd = await XB.get(`/api/commands/${id}`); } catch (e) { continue; }
      if (cmd.status !== "pending" && cmd.status !== "accepted") break;
    }
    box.remove();
    const map = { done: ["good", "hecho"], rejected: ["warning", "rechazado por el bot"], expired: ["warning", "caducado sin ejecutar"], failed: ["critical", "falló"] };
    const [lvl, word] = map[cmd.status] || ["warning", "sin respuesta del bot (no se ha confirmado su ejecución)"];
    XB.feedback(lvl, `${CMD_LABEL[type]}: ${word}`, cmd.result || "");
    XB.refresh().catch(() => {});
    return cmd;
  };

  // Flujos de confirmación de la barra de control
  XB.runFlow = async function (type) {
    if (XB.readOnly) return;
    if (!XB.controlActive()) { const ok = await XB.unlock(); if (!ok) return; }   // primero, desbloquear
    const snap = XB.state.snapshot;
    const pos = snap.position;
    if (type === "pause") {
      if (await XB.confirm({ title: "Pausar nuevas entradas", okLabel: "⏸ Pausar",
        body: [h("p", null, "El bot dejará de abrir posiciones nuevas. La posición abierta, si la hay, sigue protegida por su stop y su take profit en Kraken.")] }))
        XB.sendCommand("pause");
    } else if (type === "resume") {
      if (await XB.confirm({ title: "Reanudar entradas", okLabel: "▶ Reanudar",
        body: [h("p", null, "El bot volverá a abrir posiciones cuando haya señal y los circuit breakers lo permitan.")] }))
        XB.sendCommand("resume");
    } else if (type === "rearm") {
      if (await XB.confirm({ title: "Rearmar el bot", okLabel: "🔄 Rearmar",
        body: [h("p", null, h("strong", { text: "Motivo de la parada: " }), snap.bot.reason || "—"),
          h("p", null, "Antes de rearmar, comprueba en Kraken que la cuenta está como esperas. Una parada por límite diario solo se puede rearmar tras el reinicio de las 00:00 UTC.")] }))
        XB.sendCommand("rearm");
    } else if (type === "close_position") {
      if (!pos) return XB.feedback("info", "Sin posición", "No hay ninguna posición abierta que cerrar.");
      const r = await XB.confirm({ title: "Cerrar la posición a mercado", okLabel: "✖ Cerrar posición", okClass: "btn-danger",
        steps: { labels: ["Revisar", "Confirmar"], current: 1 }, word: "CERRAR",
        body: [h("p", null, "Se cerrarán ", h("strong", { text: `${num(pos.size, 0)} XRP` }), ` (≈ ${XB.usd(pos.notional_usd)}) a precio de mercado, se cancelarán el stop y el take profit y `,
          h("strong", { text: "se pausarán las entradas" }), ". Para volver a operar tendrás que pulsar «Reanudar».")] });
      if (r) XB.sendCommand("close_position", {}, r.word);
    } else if (type === "kill") {
      const step1 = await XB.confirm({ title: "KILL SWITCH", okLabel: "Continuar", okClass: "btn-danger",
        steps: { labels: ["Entender", "Confirmar"], current: 0 },
        body: [h("p", null, "El kill switch:"), h("ul", { class: "reasons" },
          h("li", null, "cancela TODAS las órdenes del símbolo,"),
          h("li", null, "cierra cualquier posición a mercado (con el deslizamiento que haya),"),
          h("li", null, "y deja el bot DETENIDO hasta un rearme manual."))] });
      if (!step1) return;
      const r = await XB.confirm({ title: "Confirmar KILL SWITCH", okLabel: "⛔ Activar kill switch", okClass: "btn-danger",
        steps: { labels: ["Entender", "Confirmar"], current: 1 }, word: "KILL",
        body: [h("p", null, "Esta acción no se puede deshacer desde la interfaz.")] });
      if (r) XB.sendCommand("kill", {}, r.word);
    }
  };

  XB.updateControlbar = function (st) {
    const bar = $("#controlbar");
    if (!bar) return;
    const snap = st.snapshot, state = snap.bot.effective_state;
    const alive = !["down", "unknown"].includes(state);
    const show = (id, v) => { const b = $(id); if (b) b.hidden = !v; };
    show("#btn-pause", state === "active" || state === "reconnecting");
    show("#btn-resume", state === "paused");
    show("#btn-rearm", state === "halted" || state === "frozen");
    const hasPos = !!snap.position && !snap.position.unknown;
    for (const b of $$("[data-cmd]", bar)) {
      let why = null;
      if (!alive) why = "El bot no responde: usa la CLI en su máquina";
      else if (b.dataset.cmd === "close_position" && !hasPos) why = "No hay posición abierta";
      b.disabled = !!why;
      b.title = why || (XB.controlActive() ? "" : "Pedirá desbloquear el control con tu contraseña");
    }
  };
  document.addEventListener("click", (e) => {
    const b = e.target.closest("#controlbar [data-cmd]");
    if (b && !b.disabled) XB.runFlow(b.dataset.cmd);
  });

  // ---------------------------------------------------------------- cabecera: tema, zona horaria, sesión, demo
  $("#theme-toggle")?.addEventListener("click", () => {
    const light = document.documentElement.dataset.theme !== "light";
    if (light) document.documentElement.dataset.theme = "light"; else delete document.documentElement.dataset.theme;
    setPref("theme", light ? "light" : "dark");
    document.dispatchEvent(new CustomEvent("themechange"));
  });
  const tzBtn = $("#tz-toggle");
  function renderTz() { if (tzBtn) tzBtn.textContent = XB.tz === "local" ? "Madrid" : "UTC"; }
  tzBtn?.addEventListener("click", () => {
    XB.tz = XB.tz === "local" ? "utc" : "local"; setPref("tz", XB.tz); renderTz();
    for (const fn of listeners) { try { fn(XB.state); } catch (e) { console.error(e); } }
    document.dispatchEvent(new CustomEvent("tzchange"));
  });
  renderTz();
  $("#logout-btn")?.addEventListener("click", async () => { await XB.post("/logout").catch(() => {}); location.href = "/login"; });
  $("#scenario")?.addEventListener("change", async (e) => {
    try { await XB.post("/api/demo/scenario", { name: e.target.value }); location.reload(); }
    catch (ex) { XB.feedback("critical", "Escenario", ex.message); }
  });
  $("#more-btn")?.addEventListener("click", (e) => { e.preventDefault(); $("#more-dialog").showModal(); });

  // ---------------------------------------------------------------- arranque
  document.addEventListener("DOMContentLoaded", () => {
    renderHeader(XB.state);
    renderLock();
    const p = XB.pages[XB.page];
    if (p && p.init) p.init();
    tick();
    setInterval(tick, 1000);
    connect();
  });
})();
