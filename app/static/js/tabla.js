// Herramientas de tabla reutilizables: columnas visibles (recordadas en este navegador) y
// exportación a CSV que abre bien en Excel en español (separador ";" y BOM UTF-8).
import { REG } from "./base.js";

export function columnasVisibles(tabla, lista, columnas, clave) {
  let ocultas = new Set();
  try { ocultas = new Set(JSON.parse(localStorage.getItem(clave) || "[]")); } catch (e) { /* sin almacenamiento */ }
  const aplicar = () => {
    for (const c of columnas) tabla.classList.toggle(`sin-${c.id}`, ocultas.has(c.id));
    try { localStorage.setItem(clave, JSON.stringify([...ocultas])); } catch (e) { /* modo privado */ }
  };
  lista.innerHTML = columnas.map((c) => `<label><input type="checkbox" value="${c.id}" ${ocultas.has(c.id) ? "" : "checked"}> ${c.nombre}</label>`).join("");
  lista.addEventListener("change", (e) => {
    const c = e.target.value;
    if (e.target.checked) ocultas.delete(c); else ocultas.add(c);
    REG.debug(`Columna ${c} ${e.target.checked ? "visible" : "oculta"}`);
    aplicar();
  });
  aplicar();
}

const celda = (v) => {
  if (v == null) return "";
  const t = String(v);
  return /[";\n\r]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t;
};

export function descargarCSV(nombre, encabezados, filas) {
  const texto = "﻿" + [encabezados, ...filas].map((f) => f.map(celda).join(";")).join("\r\n");
  const url = URL.createObjectURL(new Blob([texto], { type: "text/csv;charset=utf-8" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: nombre });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// Pide todas las páginas de un listado (100 por vez) hasta `maximo` filas, informando el avance.
export async function traerTodo(api, ruta, parametros, { maximo = 10000, avance } = {}) {
  const filas = [];
  for (let pagina = 1; ; pagina++) {
    const q = new URLSearchParams({ ...parametros, pagina, por_pagina: 100 });
    const r = await api(`${ruta}?${q}`);
    filas.push(...r.avisos);
    if (avance) avance(filas.length, r.total);
    if (filas.length >= Math.min(r.total, maximo) || !r.avisos.length) return { filas: filas.slice(0, maximo), total: r.total };
  }
}

export function marcarOrden(tabla, orden) {
  const col = orden.replace(/^-/, "");
  for (const b of tabla.querySelectorAll("button.orden")) {
    const th = b.closest("th");
    const activa = b.dataset.orden === col;
    th.setAttribute("aria-sort", activa ? (orden.startsWith("-") ? "descending" : "ascending") : "none");
  }
}

export const cuentaFiltros = (form, campos) => campos.filter((k) => new FormData(form).get(k)).length;
