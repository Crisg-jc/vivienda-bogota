// Componentes de interacción compartidos: avisos emergentes, panel lateral y botones con carga.
import { $, esc, REG } from "./base.js";

// Aviso emergente: confirma que algo pasó (copiado, exportado, fijado) o explica un error.
export function toast(texto, { tipo = "exito", duracion = 3800 } = {}) {
  const caja = $("#toasts");
  if (!caja) return;
  const t = document.createElement("div");
  t.className = "toast";
  t.dataset.tipo = tipo;
  t.innerHTML = `<span class="toast__ico" aria-hidden="true">${{ error: "!", info: "i", advertencia: "!" }[tipo] || "✓"}</span><span>${esc(texto)}</span>`;
  caja.append(t);
  setTimeout(() => t.remove(), duracion);
}

// Ejecuta una acción larga mostrando el botón ocupado; el botón no se puede volver a pulsar mientras tanto.
export async function ocupado(boton, accion) {
  boton.setAttribute("aria-busy", "true");
  boton.disabled = true;
  try { return await accion(); }
  finally { boton.removeAttribute("aria-busy"); boton.disabled = false; }
}

// Panel lateral accesible: atrapa el foco, se cierra con Esc o el fondo y devuelve el foco a quien lo abrió.
let origen = null;
let alCerrar = null;
const enfocables = 'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])';

export function abrirPanel(titulo, html, { cerrar } = {}) {
  origen = document.activeElement;
  alCerrar = cerrar || null;
  $("#panel-titulo").textContent = titulo;
  $("#panel-cuerpo").innerHTML = html;
  $("#panel").hidden = false;
  $("#panel-fondo").hidden = false;
  document.body.style.overflow = "hidden";
  $("#panel").focus();
  REG.debug(`Panel abierto: ${titulo}`);
  return $("#panel-cuerpo");
}

export function cerrarPanel() {
  if ($("#panel").hidden) return;
  $("#panel").hidden = true;
  $("#panel-fondo").hidden = true;
  document.body.style.overflow = "";
  if (alCerrar) { try { alCerrar(); } catch (e) { REG.error("Falló al cerrar el panel", e); } }
  if (origen && document.contains(origen)) origen.focus();
}

export function iniciarPanel() {
  $("#panel-cerrar").addEventListener("click", cerrarPanel);
  $("#panel-fondo").addEventListener("click", cerrarPanel);
  document.addEventListener("keydown", (e) => {
    if ($("#panel").hidden) return;
    if (e.key === "Escape") { e.preventDefault(); cerrarPanel(); return; }
    if (e.key !== "Tab") return;
    const lista = [...$("#panel").querySelectorAll(enfocables)].filter((x) => x.offsetParent !== null);
    if (!lista.length) return;
    const primero = lista[0], ultimo = lista[lista.length - 1];
    if (e.shiftKey && document.activeElement === primero) { e.preventDefault(); ultimo.focus(); }
    else if (!e.shiftKey && document.activeElement === ultimo) { e.preventDefault(); primero.focus(); }
  });
}

// Copiar al portapapeles con respaldo para navegadores sin la API moderna.
export async function copiar(texto) {
  try {
    await navigator.clipboard.writeText(texto);
  } catch (e) {
    const area = document.createElement("textarea");
    area.value = texto; area.setAttribute("readonly", ""); area.style.position = "fixed"; area.style.opacity = "0";
    document.body.append(area); area.select();
    document.execCommand("copy");
    area.remove();
  }
}
