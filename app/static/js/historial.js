import { esc, fechaDia, fmt, m2 } from "./base.js";

// Línea de tiempo de un aviso (/api/historia), para el panel de detalle.
export function historiaHTML(h) {
  if (!h.eventos.length) return `<p class="como-leer">${esc(h.mensaje || "Sin historia registrada para este aviso.")}</p>`;
  return `<ol class="linea-tiempo">${h.eventos.map((e) => `<li class="ev ev--${esc(e.tipo)}">
      <time>${e.fecha ? fechaDia(e.fecha) : "—"}</time> ${esc(e.texto || e.tipo)}</li>`).join("")}</ol>
    <p class="como-leer">${h.dias_publicado != null ? `${h.disponible ? "Lleva" : "Estuvo"} ${fmt.format(h.dias_publicado)} días publicado (desde la ${esc(h.desde_fuente)}).` : ""}</p>`;
}

export function lineaTendencia(semanas) {
  const v = semanas.filter((s) => s.suficiente);
  const W = 320, H = 90, P = 8;
  const min = Math.min(...v.map((s) => s.mediana_m2)), max = Math.max(...v.map((s) => s.mediana_m2));
  const x = (i) => P + (i * (W - 2 * P)) / Math.max(1, v.length - 1);
  const y = (m) => H - P - ((m - min) / Math.max(1, max - min)) * (H - 2 * P);
  const puntos = v.map((s, i) => `${x(i).toFixed(1)},${y(s.mediana_m2).toFixed(1)}`).join(" ");
  return `<svg class="tendencia__svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="m² mediano por semana, de ${m2(v[0].mediana_m2)} a ${m2(v[v.length - 1].mediana_m2)}">
    <polyline points="${puntos}" fill="none" stroke="currentColor" stroke-width="2"/>
    ${v.map((s, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(s.mediana_m2).toFixed(1)}" r="3"><title>Semana del ${fechaDia(s.semana)}: ${m2(s.mediana_m2)} (${s.n} avisos)</title></circle>`).join("")}
  </svg>`;
}
