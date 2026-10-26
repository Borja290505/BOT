/* xrpbot · service worker (PWA)
 * - Guarda en caché SOLO la carcasa estática (CSS, JS, iconos y la página sin conexión).
 * - NUNCA guarda /api ni las páginas: llevan datos del bot y el token de sesión;
 *   sin red se muestra la página «Sin conexión», jamás datos viejos como si fueran actuales.
 * - Recibe las notificaciones push y abre la pantalla indicada al tocarlas.
 */
"use strict";
const VERSION = "xrpbot-shell-v1";
const SHELL = [
  "/static/offline.html",
  "/static/css/tokens.css", "/static/css/components.css", "/static/css/app.css",
  "/static/js/theme.js", "/static/js/pwa.js",
  "/static/icons/icon-192.png", "/static/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;                 // datos: siempre red
  if (req.mode === "navigate") {                                 // páginas: red; sin red, aviso
    event.respondWith(fetch(req).catch(() => caches.match("/static/offline.html")));
    return;
  }
  if (url.pathname.startsWith("/static/")) {                     // estáticos: red primero, caché si no hay
    event.respondWith(fetch(req).then((res) => {
      if (res.ok && SHELL.includes(url.pathname)) {
        const copy = res.clone();
        caches.open(VERSION).then((c) => c.put(req, copy));
      }
      return res;
    }).catch(() => caches.match(req)));
  }
});

self.addEventListener("push", (event) => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch (e) { d = { title: "xrpbot", body: event.data && event.data.text() }; }
  const title = d.title || "xrpbot";
  event.waitUntil(self.registration.showNotification(title, {
    body: d.body || "",
    tag: d.tag || undefined,                  // mismo tag = sustituye al aviso anterior del mismo tipo
    renotify: !!d.tag,
    requireInteraction: d.level === "critical",
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/icon-192.png",
    data: { url: d.url || "/" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL((event.notification.data && event.notification.data.url) || "/", self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
    for (const w of wins) {
      if ("focus" in w) { w.navigate(target).catch(() => {}); return w.focus(); }
    }
    return self.clients.openWindow(target);
  }));
});
