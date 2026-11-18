// Registro (logs) del navegador. Se carga antes que todo lo demás y en JavaScript antiguo a propósito:
// así atrapa también los errores de navegadores viejos y los fallos al cargar app.js o Leaflet.
//
// Lo que pasa en la página se escribe en la consola del navegador (F12 > Consola) con el prefijo
// [vivienda] y se envía al servidor, que lo guarda en datos/registros/vivienda.log junto a sus
// propios mensajes (logger "vivienda.navegador"). Así un error del navegador se ve en el mismo
// archivo que el error de la API que lo causó (mismo rid).
//
// Modo detallado: abrir la página con ?depurar=1 (queda recordado; ?depurar=0 lo apaga).
// En ese modo también se registran y envían los mensajes de depuración (cada paso).
(function () {
  "use strict";
  var NIVELES = { debug: 10, info: 20, warn: 30, error: 40 };
  var sesion = Math.random().toString(36).slice(2, 10);
  var cola = [];
  var enviando = false;
  var temporizador = null;
  var enviados = 0;
  var depurar = false;
  var yaEnviado = false;

  try {
    var m = /[?&]depurar=([01])/.exec(location.search);
    if (m) localStorage.setItem("vivienda-depurar", m[1]);
    depurar = localStorage.getItem("vivienda-depurar") === "1";
  } catch (e) { /* almacenamiento bloqueado (modo privado): sin modo detallado recordado */ }

  function texto(v) {
    if (v === undefined) return "";
    if (v instanceof Error) return v.name + ": " + v.message + (v.stack ? "\n" + v.stack : "");
    if (typeof v === "string") return v;
    try { return JSON.stringify(v); } catch (e) { return String(v); }
  }

  function enviar(final) {
    if (!cola.length || (enviando && !final)) return;
    var lote = cola.splice(0, 50);
    var cuerpo = JSON.stringify(lote);
    enviados += lote.length;
    // Al cerrar la página solo sendBeacon garantiza la entrega.
    if (final && navigator.sendBeacon) {
      try { navigator.sendBeacon("/api/registro-cliente", new Blob([cuerpo], { type: "application/json" })); return; } catch (e) {}
    }
    enviando = true;
    try {
      var x = new XMLHttpRequest();
      x.open("POST", "/api/registro-cliente", true);
      x.setRequestHeader("Content-Type", "application/json");
      x.onloadend = function () { enviando = false; if (cola.length) programar(); };
      x.send(cuerpo);
    } catch (e) { enviando = false; }
  }

  function programar() {
    if (temporizador) return;
    temporizador = setTimeout(function () { temporizador = null; enviar(false); }, 1500);
  }

  function registrar(nivel, mensaje, detalle) {
    var consola = window.console || {};
    var metodo = consola[nivel === "debug" ? "debug" : nivel] || consola.log;
    if (nivel !== "debug" || depurar) {
      try { metodo.call(consola, "[vivienda] " + mensaje, detalle === undefined ? "" : detalle); } catch (e) {}
    }
    if (NIVELES[nivel] < (depurar ? 10 : 20)) return;
    if (enviados > 1000) return;
    var primero = !yaEnviado; yaEnviado = true;     // una página que entró en bucle no debe inundar el servidor
    cola.push({
      nivel: nivel, mensaje: String(mensaje).slice(0, 2000), detalle: detalle === undefined ? null : texto(detalle).slice(0, 6000),
      pagina: (location.pathname + location.search + location.hash).slice(0, 500),
      // El navegador completo va en el primer mensaje y en los avisos y errores; repetirlo en cada línea solo estorba.
      navegador: (primero || NIVELES[nivel] >= 30) ? navigator.userAgent.slice(0, 400) : null, sesion: sesion
    });
    if (nivel === "error") enviar(false); else programar();
  }

  // Cronómetro: var t = REG.tiempo("Cargar zonas"); ...; t.fin({ zonas: 20 })
  function tiempo(que) {
    var inicio = (window.performance && performance.now) ? performance.now() : Date.now();
    registrar("debug", "Inicio: " + que);
    return {
      fin: function (extra) {
        var ms = ((window.performance && performance.now) ? performance.now() : Date.now()) - inicio;
        registrar(ms > 3000 ? "warn" : "debug", "Listo: " + que + " en " + Math.round(ms) + " ms" + (ms > 3000 ? " (lento)" : ""), extra);
        return ms;
      },
      fallo: function (error) {
        var ms = ((window.performance && performance.now) ? performance.now() : Date.now()) - inicio;
        registrar("error", "Falló: " + que + " tras " + Math.round(ms) + " ms", error);
      }
    };
  }

  window.REG = {
    debug: function (m, d) { registrar("debug", m, d); },
    info: function (m, d) { registrar("info", m, d); },
    warn: function (m, d) { registrar("warn", m, d); },
    error: function (m, d) { registrar("error", m, d); },
    tiempo: tiempo,
    sesion: sesion,
    depurar: function () { return depurar; }
  };

  // Errores de JavaScript que nadie atrapó.
  window.addEventListener("error", function (ev) {
    var t = ev.target;
    if (t && t !== window && (t.src || t.href)) {
      // Un archivo (script, hoja de estilos, imagen) que no cargó.
      var url = t.src || t.href;
      if (/basemaps\.cartocdn\.com/.test(url)) return;   // teselas del mapa: se cuentan aparte
      registrar("error", "No cargó el archivo " + url + " (<" + t.tagName.toLowerCase() + ">)");
      return;
    }
    registrar("error", "Error de JavaScript: " + (ev.message || "desconocido") + " en " +
      (ev.filename || "?") + ":" + (ev.lineno || "?") + ":" + (ev.colno || "?"), ev.error);
  }, true);

  window.addEventListener("unhandledrejection", function (ev) {
    registrar("error", "Promesa rechazada sin atrapar: " + (ev.reason && ev.reason.message ? ev.reason.message : texto(ev.reason)), ev.reason);
  });

  window.addEventListener("pagehide", function () { registrar("debug", "Página cerrada u oculta"); enviar(true); });
  window.addEventListener("online", function () { registrar("info", "El navegador recuperó la conexión"); });
  window.addEventListener("offline", function () { registrar("warn", "El navegador perdió la conexión"); });

  var pantalla = window.screen ? screen.width + "x" + screen.height : "?";
  var zona = "?";
  try { zona = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) {}
  registrar("info", "Página abierta: ventana " + window.innerWidth + "x" + window.innerHeight + ", pantalla " + pantalla +
    ", escala " + (window.devicePixelRatio || 1) + ", idioma " + (navigator.language || "?") + ", zona " + zona +
    (depurar ? ", MODO DETALLADO" : ""));
  if (!("noModule" in document.createElement("script"))) {
    registrar("error", "Navegador sin soporte de módulos de JavaScript: la aplicación no puede funcionar aquí");
  }
})();
