// Falla si estilos.css usa algo que algún navegador de .browserslistrc no entiende.
// Las degradaciones aceptadas (se ven un poco distinto pero funcionan) van en ACEPTADAS.
const fs = require("fs");
const postcss = require("postcss");
const doiuse = require("doiuse");

const ACEPTADAS = new Set([
  "css-scroll-behavior",   // sin desplazamiento suave: salta directo
  "css-focus-visible",     // se ve el foco por defecto del navegador
  "flexbox-gap",           // Safari 14.0: menos espacio entre elementos
  "css-backdrop-filter",   // Firefox < 103: barra sin desenfoque, igual de legible
  "css-overflow", "css-sticky", "css-marker-pseudo", "css-display-contents",
  "css-clip-path", "css-masks", "css-gradients", "extended-system-fonts",
  "css-paged-media",       // solo la hoja de impresión: sin @page, el navegador usa sus márgenes
  "multicolumn",           // break-inside en impresión: una tarjeta puede partirse entre páginas
  "css-scrollbar",         // ocultar la barra de desplazamiento del menú en tableta: si no, se ve una barra fina
  "css3-cursors",          // cursor "no permitido" en botones deshabilitados: en táctil no hay cursor
]);
const problemas = [];
postcss([doiuse({ onFeatureUsage: (u) => { if (!ACEPTADAS.has(u.feature)) problemas.push(u.message); } })])
  .process(fs.readFileSync("app/static/estilos.css", "utf8"), { from: "app/static/estilos.css" })
  .then(() => {
    if (problemas.length) { console.error(problemas.join("\n")); process.exit(1); }
    console.log("CSS compatible con los navegadores de .browserslistrc");
  });
