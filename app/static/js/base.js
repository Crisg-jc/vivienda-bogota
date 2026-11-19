// Utilidades comunes de la interfaz: formato, escape de HTML, fechas y acceso a la API (con registro).
export const $ = (s, r = document) => r.querySelector(s);

export const ultimo = (lista) => lista[lista.length - 1];   // Array.prototype.at no existe en Safari < 15.4

export const NS = "http://www.w3.org/2000/svg";

export const reducirMovimiento = matchMedia("(prefers-reduced-motion: reduce)").matches;

// ---------------------------------------------------------------- formato
export const fmt = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 0 });

export const fmt1 = new Intl.NumberFormat("es-CO", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

export const millones = (v) => (v >= 1e9 ? `$${fmt.format(Math.round(v / 1e6))} millones` : `$${fmt.format(Math.round(v / 1e6))} millones`);

export const corto = (v) => `$${fmt.format(Math.round(v / 1e6))} M`;

export const m2 = (v) => `$${fmt1.format(v / 1e6)} M`;

export const area = (v) => `${fmt.format(v)} m²`;

// Todo texto que viene de los datos pasa por esc() antes de entrar en HTML: un barrio o una localidad
// con "<script>" en un CSV ajeno se muestra como texto, nunca se ejecuta.
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

export const urlSegura = (u) => (typeof u === "string" && /^https?:\/\//.test(u) ? u : null);

// Registro del navegador (registro.js). Si ese archivo no cargó, se usa la consola sin más.
export const REG = window.REG || { debug() {}, info: console.info.bind(console), warn: console.warn.bind(console),
  error: console.error.bind(console), tiempo: () => ({ fin() {}, fallo() {} }) };

let contadorPeticiones = 0;

// Toda llamada a la API pasa por aquí: se registra método, ruta, código, duración y el rid que la
// enlaza con la línea del servidor en datos/registros/vivienda.log.
export async function api(ruta, opciones = {}) {
  const metodo = opciones.method || "GET";
  const rid = `${REG.sesion || "nav"}-${++contadorPeticiones}`;
  const inicio = performance.now();
  const sondeo = ruta === "/api/automatizacion";
  REG.debug(`→ ${metodo} ${ruta}`, opciones.body ? JSON.parse(opciones.body) : undefined);
  let r;
  try {
    r = await fetch(ruta, { ...opciones, headers: { ...(opciones.headers || {}), "X-Request-ID": rid } });
  } catch (e) {
    REG.error(`Sin respuesta del servidor en ${metodo} ${ruta} (rid=${rid}): ¿la aplicación está cerrada?`, e);
    throw new Error("No hay conexión con la aplicación. ¿Sigue abierta la ventana donde la inició?");
  }
  const ms = Math.round(performance.now() - inicio);
  if (!r.ok) {
    let detalle = `Error ${r.status}`, cuerpo = null;
    try { cuerpo = await r.json(); detalle = typeof cuerpo.detail === "string" ? cuerpo.detail : cuerpo.detail?.[0]?.msg || detalle; } catch {}
    REG[r.status >= 500 ? "error" : "warn"](`${metodo} ${ruta} -> ${r.status} en ${ms} ms (rid=${rid}): ${detalle}`, cuerpo);
    throw new Error(detalle);
  }
  let datos;
  try {
    datos = await r.json();
  } catch (e) {
    REG.error(`${metodo} ${ruta}: la respuesta no es JSON válido (rid=${rid})`, e);
    throw new Error("La aplicación devolvió una respuesta dañada.");
  }
  if (!sondeo) REG.debug(`← ${metodo} ${ruta} ${r.status} en ${ms} ms`);
  if (ms > 3000) REG.warn(`Respuesta lenta: ${metodo} ${ruta} tardó ${ms} ms (rid=${rid})`);
  return datos;
}

// Ejecuta una carga y, si falla, la registra y muestra el problema en su sección en vez de dejarla vacía.
export async function seguro(que, fn, alFallar) {
  const t = REG.tiempo(que);
  try {
    const v = await fn();
    t.fin();
    return v;
  } catch (e) {
    t.fallo(e);
    if (alFallar) {
      try { alFallar(e); } catch (e2) { REG.error(`También falló el aviso de error de ${que}`, e2); }
    }
    return undefined;
  }
}

export function el(nombre, atributos = {}, padre) {
  const n = document.createElementNS(NS, nombre);
  for (const [k, v] of Object.entries(atributos)) n.setAttribute(k, v);
  if (padre) padre.appendChild(n);
  return n;
}

// ---------------------------------------------------------------- comparación con lo esperado
export const pct = (v) => `${v > 0 ? "+" : "−"}${fmt.format(Math.abs(Math.round(v * 100)))} %`;

export const pctEntero = (v) => `${fmt.format(Math.round(v * 100))} %`;

export const pctTexto = (n, total) => `${fmt.format(Math.round((n / total) * 100))} %`;

export const NIVEL = { alta: "Confiabilidad alta", media: "Confiabilidad media", baja: "Confiabilidad baja", insuficiente: "Sin información suficiente" };

export const RESULTADOS = { completo: "Completo", bloqueado: "Bloqueado por el portal", sin_portales: "Sin portales autorizados",
  error: "Error", sin_conexion: "Sin conexión", en_curso: "Otro ciclo en curso" };

export const hora = (iso) => new Date(iso).toLocaleTimeString("es-CO", { hour: "numeric", minute: "2-digit" });

export const fechaCorta = (iso) => new Date(iso).toLocaleString("es-CO", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });

export const fechaLarga = (iso) => new Date(iso).toLocaleString("es-CO", { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" });

export const diaLargo = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString("es-CO", { day: "numeric", month: "short", year: "numeric" });

export function haceCuanto(iso) {
  if (!iso) return "sin fecha";
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (min < 2) return "hace un momento";
  if (min < 60) return `hace ${min} min`;
  const h = Math.round(min / 60);
  if (h < 24) return `hace ${h} h`;
  const d = Math.round(h / 24);
  return d === 1 ? "hace 1 día" : `hace ${d} días`;
}

// ---------------------------------------------------------------- historial
export const fechaDia = (iso) => new Date(iso.length <= 10 ? `${iso}T12:00:00` : iso).toLocaleDateString("es-CO", { day: "numeric", month: "short", year: "numeric" });

// ---------------------------------------------------------------- trazabilidad de cada aviso
export const NOMBRE_PORTAL = { fincaraiz: "Finca Raíz", ciencuadras: "Ciencuadras", metrocuadrado: "Metrocuadrado" };

// Colores de la paleta leídos del CSS en el momento: cambian con el modo oscuro.
export const tono = (variable) => getComputedStyle(document.documentElement).getPropertyValue(variable).trim();
export const temaOscuro = () => document.documentElement.getAttribute("data-tema-efectivo") === "oscuro";
// Calles de fondo: claras u oscuras según el tema
export const teselas = () => `https://{s}.basemaps.cartocdn.com/${temaOscuro() ? "dark_all" : "light_all"}/{z}/{x}/{y}{r}.png`;
