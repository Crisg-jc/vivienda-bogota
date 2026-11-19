import { $, corto, esc, fmt, fmt1, NIVEL } from "./base.js";

// ¿Cuánto aciertan de verdad? Prueba con avisos reales estimados como si fueran nuevos.
export function pintarPrecision(m) {
  const pr = m.precision, caja = $("#mo-precision");
  if (!pr || !pr.muestra) { caja.innerHTML = ""; return; }
  const pct = (v) => (v == null ? "—" : `${fmt1.format(v * 100)} %`);
  const orden = ["alta", "media", "baja", "insuficiente"].filter((k) => pr.niveles[k]);
  const filas = orden.map((k) => {
    const n = pr.niveles[k];
    return `<tr><td>${NIVEL[k]}</td><td class="num">${fmt.format(n.n)}</td><td class="num">${pct(n.error_comparables)}</td>
      <td class="num">${pct(n.error_modelo)}</td><td class="num">${pct(n.error_mezcla)}</td>
      <td class="num">${n.peso_comparables == null ? "—" : `${Math.round(n.peso_comparables * 100)} %`}</td>
      <td class="num">${pct(n.cobertura_rango_comparables)}</td></tr>`;
  }).join("");
  const loc = (m.por_localidad || []).map((z) => `<tr><td>${esc(z.localidad)}</td><td class="num">${fmt.format(z.n)}</td><td class="num">${pct(z.error_mediano)}</td></tr>`).join("");
  caja.innerHTML = `
    <h3>¿Cuánto aciertan de verdad?</h3>
    <p class="como-leer">${esc(pr.metodo)} Muestra: ${fmt.format(pr.muestra)} avisos. Si la confiabilidad funciona, el error debería bajar de "baja" a "alta".</p>
    <div class="desliza" tabindex="0" role="region" aria-label="Precisión por nivel de confiabilidad"><table class="tabla">
      <thead><tr><th>Nivel</th><th class="num">Pruebas</th><th class="num">Error comparables</th><th class="num">Error modelo</th><th class="num">Error mezcla</th><th class="num">Peso comparables</th><th class="num">Acierto del rango de solo comparables</th></tr></thead>
      <tbody>${filas}</tbody></table></div>
    ${loc ? `<details class="precision__zonas"><summary>Error del modelo por localidad</summary><p class="como-leer">Mediana del error con avisos que el modelo no vio. Solo localidades con 5 avisos o más.</p>
    <div class="desliza" tabindex="0" role="region" aria-label="Error por localidad"><table class="tabla"><thead><tr><th>Localidad</th><th class="num">Avisos</th><th class="num">Error mediano</th></tr></thead><tbody>${loc}</tbody></table></div></details>` : ""}
    ${m.atipicos_excluidos ? `<p class="como-leer">${fmt.format(m.atipicos_excluidos)} avisos marcados como atípicos en la limpieza no entraron al entrenamiento.</p>` : ""}`;
}

export function mezclaTexto(c, r) {
  const m = c.mezcla;
  if (!m) return "";
  const pc = Math.round(m.peso_comparables * 100);
  const como = pc === 100 ? "solo de los comparables" : pc === 0 ? "solo del modelo (aquí los comparables acertaron menos)"
    : `${pc} % de los comparables y ${100 - pc} % del modelo`;
  const prueba = m.calibrado
    ? `Así se fijó porque, en una prueba con ${fmt.format(m.pruebas)} avisos reales de confiabilidad ${esc(c.confiabilidad)} estimados como si fueran nuevos, esa mezcla fue la que menos falló (error mediano ${fmt1.format((m.error_tipico || 0) * 100)} %). El rango ${corto(r.minimo)} a ${corto(r.maximo)} es el que en esa prueba contuvo el precio real 8 de cada 10 veces.`
    : `Todavía no hay suficientes pruebas con avisos reales de confiabilidad ${esc(c.confiabilidad)} para medir la mejor mezcla; se usa una por defecto y el rango del modelo${c.confiabilidad === "baja" ? ", ampliado" : ""}.`;
  return `<p>Precio final = ${como}: ${corto(r.precio)}. ${prueba}</p>`;
}
