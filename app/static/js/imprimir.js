// "Descargar en PDF": usa la impresión del navegador (Guardar como PDF), con una hoja hecha para eso:
// datos del apartamento, precio y rango, cómo se obtuvo, comparables y de dónde salen los datos.
import { $, esc, fmt, REG } from "./base.js";

export function iniciarImpresion(obtener) {
  // También con Ctrl+P o el menú del navegador: la cabecera se arma justo antes de imprimir.
  const preparar = () => {
    const { datos, dataset, verificacion } = obtener();
    const fecha = new Date().toLocaleString("es-CO", { dateStyle: "long", timeStyle: "short" });
    const desc = [`${fmt.format(datos.area_m2)} m²`, `estrato ${datos.estrato}`, datos.localidad || "sin localidad",
      datos.habitaciones != null ? `${datos.habitaciones} habitaciones` : null, datos.banos != null ? `${datos.banos} baños` : null,
      datos.parqueaderos != null ? `${datos.parqueaderos} parqueaderos` : null, datos.antiguedad != null ? `${datos.antiguedad} años` : null]
      .filter(Boolean).join(" · ");
    $("#impresion").innerHTML = `<h1>Estimación del precio de publicación</h1>
      <p><strong>${esc(desc)}</strong></p>
      <p>Generada el ${esc(fecha)} con ${fmt.format(dataset?.avisos ?? 0)} avisos reales${verificacion?.ultima_verificacion
        ? ` (última verificación con la fuente: ${esc(new Date(verificacion.ultima_verificacion).toLocaleString("es-CO", { dateStyle: "medium", timeStyle: "short" }))})` : ""}.
      Es una referencia del precio de publicación, no un avalúo.</p>
      <p class="impresion__enlace">Para volver a verla: ${esc(location.href)}</p>`;
    REG.info("Estimación enviada a imprimir / PDF", datos);
  };
  window.addEventListener("beforeprint", preparar);
  $("#imprimir").addEventListener("click", () => { preparar(); window.print(); });
}
