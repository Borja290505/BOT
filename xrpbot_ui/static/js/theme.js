// Se carga en <head> para aplicar el tema antes de pintar (sin parpadeo). Oscuro por defecto.
(function () {
  try {
    if (localStorage.getItem("xrpbot-theme") === "light") document.documentElement.dataset.theme = "light";
  } catch (e) { /* almacenamiento no disponible: tema oscuro */ }
})();
