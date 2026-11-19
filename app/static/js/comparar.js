// Comparar dos apartamentos: se fija una estimación y la siguiente se muestra al lado, con las
// diferencias de precio, m², rango, confiabilidad y características. La fijada se recuerda en este
// navegador mientras la pestaña esté abierta.
import { $, corto, esc, fmt, m2, NIVEL, REG } from "./base.js";

const CLAVE = "vivienda-comparar";
let fijada = null;
let actual = null;

const describir = (d) => [
  `${fmt.format(d.area_m2)} m²`, `estrato ${d.estrato}`, d.localidad || "sin localidad",
  d.habitaciones != null ? `${d.habitaciones} hab.` : null, d.banos != null ? `${d.banos} baños` : null,
  d.parqueaderos != null ? `${d.parqueaderos} parq.` : null, d.antiguedad != null ? `${d.antiguedad} años` : null,
].filter(Boolean).join(", ");

function guardar() {
  try { fijada ? sessionStorage.setItem(CLAVE, JSON.stringify(fijada)) : sessionStorage.removeItem(CLAVE); } catch (e) { /* modo privado */ }
}

function pintar() {
  const caja = $("#comparacion"), boton = $("#fijar-comparar");
  boton.textContent = fijada ? "Quitar la fijada" : "Fijar para comparar";
  if (!fijada || !actual) { caja.hidden = true; return; }
  caja.hidden = false;
  const a = fijada, b = actual;
  const fila = (k, va, vb) => `<tr><th scope="row">${k}</th><td>${va}</td><td>${vb}</td></tr>`;
  const dif = a.precio && b.precio ? b.precio / a.precio - 1 : null;
  const texto = dif == null ? "Una de las dos no tiene precio: falta información para comparar."
    : Math.abs(dif) < 0.02 ? "Los dos valen prácticamente lo mismo."
    : `El actual se pediría ${fmt.format(Math.abs(Math.round(dif * 100)))} % ${dif > 0 ? "más" : "menos"} que el fijado (${corto(Math.abs(b.precio - a.precio))} de diferencia).`;
  const corta = (n) => (NIVEL[n] || "—").replace("Confiabilidad ", "");
  caja.innerHTML = `<h3>Comparación</h3><p>${esc(texto)}</p>
    <dl class="comparacion__pisos"><div><dt>Fijado</dt><dd>${esc(describir(a.datos))}</dd></div>
      <div><dt>Actual</dt><dd>${esc(describir(b.datos))}</dd></div></dl>
    <div class="desliza" tabindex="0" role="region" aria-label="Comparación de dos estimaciones"><table class="tabla tabla--compacta">
      <thead><tr><th></th><th>Fijado</th><th>Actual</th></tr></thead><tbody>
      ${fila("Precio", a.precio ? corto(a.precio) : "—", b.precio ? corto(b.precio) : "—")}
      ${fila("Por m²", a.precio_m2 ? m2(a.precio_m2) : "—", b.precio_m2 ? m2(b.precio_m2) : "—")}
      ${fila("Rango", a.precio ? `${corto(a.minimo)} a ${corto(a.maximo)}` : "—", b.precio ? `${corto(b.minimo)} a ${corto(b.maximo)}` : "—")}
      ${fila("Confiabilidad", esc(corta(a.confiabilidad)), esc(corta(b.confiabilidad)))}
      ${fila("Comparables usados", fmt.format(a.usados || 0), fmt.format(b.usados || 0))}
      </tbody></table></div>`;
}

export function resumen(r, datos) {
  return { datos: { ...datos }, precio: r.precio, minimo: r.minimo, maximo: r.maximo, precio_m2: r.precio_m2,
    confiabilidad: r.comparables?.confiabilidad, usados: r.comparables?.usados ?? 0 };
}

export function actualizarComparacion(r, datos) {
  actual = resumen(r, datos);
  pintar();
}

export function iniciarComparar() {
  try { fijada = JSON.parse(sessionStorage.getItem(CLAVE) || "null"); } catch (e) { fijada = null; }
  $("#fijar-comparar").addEventListener("click", () => {
    fijada = fijada ? null : actual && { ...actual };
    REG.info(fijada ? "Estimación fijada para comparar" : "Comparación quitada", fijada?.datos);
    guardar();
    pintar();
  });
  pintar();
}
