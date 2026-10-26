/* xrpbot · PWA: registro del service worker y notificaciones push.
 * En iPhone (iOS 16.4+) las notificaciones solo existen si la web se ha AÑADIDO A LA PANTALLA
 * DE INICIO y se abre desde ese icono, y el permiso solo puede pedirse tras tocar un botón.
 */
"use strict";
(function () {
  const PWA = (window.XBPWA = {});
  PWA.supported = "serviceWorker" in navigator;
  PWA.standalone = (window.matchMedia && matchMedia("(display-mode: standalone)").matches) || navigator.standalone === true;
  PWA.ios = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  PWA.pushSupported = PWA.supported && "PushManager" in window && "Notification" in window;
  PWA.secure = window.isSecureContext;

  PWA.ready = PWA.supported && PWA.secure
    ? navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch((e) => { console.warn("SW no registrado", e); return null; })
    : Promise.resolve(null);

  function b64ToBytes(b64) {
    const pad = "=".repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  /** Estado en este dispositivo: unsupported | needs-install | insecure | denied | off | on */
  PWA.status = async function () {
    if (!PWA.secure) return "insecure";
    if (PWA.ios && !PWA.standalone) return "needs-install";
    if (!PWA.pushSupported) return "unsupported";
    if (Notification.permission === "denied") return "denied";
    const reg = await PWA.ready;
    if (!reg) return "unsupported";
    const sub = await reg.pushManager.getSubscription();
    return sub ? "on" : "off";
  };

  /** Pide permiso y suscribe. Debe llamarse desde un toque del usuario. */
  PWA.enable = async function (post, get) {
    const perm = await Notification.requestPermission();
    if (perm !== "granted") throw new Error("Permiso de notificaciones no concedido");
    const reg = await PWA.ready;
    if (!reg) throw new Error("Service worker no disponible");
    const { public_key } = await get("/api/push/key");
    let sub = await reg.pushManager.getSubscription();
    if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(public_key) });
    await post("/api/push/subscribe", sub.toJSON());
  };

  PWA.disable = async function (post) {
    const reg = await PWA.ready;
    const sub = reg && (await reg.pushManager.getSubscription());
    if (!sub) return;
    await post("/api/push/unsubscribe", { endpoint: sub.endpoint }).catch(() => {});
    await sub.unsubscribe();
  };
})();
