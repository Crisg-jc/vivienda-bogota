import { api, esc, fechaDia, fmt, m2, REG } from "./base.js";

export async function mostrarHistoria(boton) {
  const fila = boton.closest("tr");
  const abierta = fila.nextElementSibling?.classList.contains("fila-historia");
  if (abierta) { fila.nextElementSibling.remove(); boton.setAttribute("aria-expanded", "false"); return; }
  boton.setAttribute("aria-expanded", "true");
  const extra = document.createElement("tr");
  extra.className = "fila-historia";
  extra.innerHTML = `<td colspan="${fila.children.length}">Cargando historial…</td>`;
  fila.after(extra);
  try {
    const h = await api(`/api/historia?url=${encodeURIComponent(boton.dataset.url)}`);
    REG.debug("Historial de un aviso", h);
    extra.firstElementChild.innerHTML = `<ol class="linea-tiempo">${h.eventos.map((e) => `<li class="ev ev--${esc(e.tipo)}">
        <time>${e.fecha ? fechaDia(e.fecha) : "—"}</time> ${esc(e.texto || e.tipo)}</li>`).join("")}</ol>
      <p class="como-leer">${h.dias_publicado != null ? `${h.disponible ? "Lleva" : "Estuvo"} ${fmt.format(h.dias_publicado)} días publicado (desde la ${esc(h.desde_fuente)}).` : ""}</p>`;
  } catch (e) {
    extra.firstElementChild.textContent = `No se pudo leer el historial: ${e.message}`;
  }
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
