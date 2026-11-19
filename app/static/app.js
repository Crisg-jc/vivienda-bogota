// Vivienda Bogotá: interfaz sin dependencias (salvo Leaflet para el mapa).
const $ = (s, r = document) => r.querySelector(s);
const ultimo = (lista) => lista[lista.length - 1];   // Array.prototype.at no existe en Safari < 15.4
const NS = "http://www.w3.org/2000/svg";
const COLOR_ESTRATO = [null, "--e1", "--e2", "--e3", "--e4", "--e5", "--e6"]
  .map((v) => v && getComputedStyle(document.documentElement).getPropertyValue(v).trim());
const reducirMovimiento = matchMedia("(prefers-reduced-motion: reduce)").matches;

// ---------------------------------------------------------------- formato
const fmt = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 0 });
const fmt1 = new Intl.NumberFormat("es-CO", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const millones = (v) => (v >= 1e9 ? `$${fmt.format(Math.round(v / 1e6))} millones` : `$${fmt.format(Math.round(v / 1e6))} millones`);
const corto = (v) => `$${fmt.format(Math.round(v / 1e6))} M`;
const m2 = (v) => `$${fmt1.format(v / 1e6)} M`;
const area = (v) => `${fmt.format(v)} m²`;

// Todo texto que viene de los datos pasa por esc() antes de entrar en HTML: un barrio o una localidad
// con "<script>" en un CSV ajeno se muestra como texto, nunca se ejecuta.
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
const urlSegura = (u) => (typeof u === "string" && /^https?:\/\//.test(u) ? u : null);

// Registro del navegador (registro.js). Si ese archivo no cargó, se usa la consola sin más.
const REG = window.REG || { debug() {}, info: console.info.bind(console), warn: console.warn.bind(console),
  error: console.error.bind(console), tiempo: () => ({ fin() {}, fallo() {} }) };
let contadorPeticiones = 0;

// Toda llamada a la API pasa por aquí: se registra método, ruta, código, duración y el rid que la
// enlaza con la línea del servidor en datos/registros/vivienda.log.
async function api(ruta, opciones = {}) {
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
async function seguro(que, fn, alFallar) {
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

function el(nombre, atributos = {}, padre) {
  const n = document.createElementNS(NS, nombre);
  for (const [k, v] of Object.entries(atributos)) n.setAttribute(k, v);
  if (padre) padre.appendChild(n);
  return n;
}

function marcasBonitas(min, max, cuantas = 5) {
  const paso0 = (max - min) / cuantas;
  const mag = 10 ** Math.floor(Math.log10(paso0));
  const paso = [1, 2, 2.5, 5, 10].map((f) => f * mag).find((p) => p >= paso0) || paso0;
  const salida = [];
  for (let v = Math.ceil(min / paso) * paso; v <= max + 1e-9; v += paso) salida.push(+v.toFixed(10));
  return salida;
}

// pseudoaleatorio con semilla, para que la dispersión de puntos no "baile" al recargar
function azar(semilla) { let s = semilla; return () => ((s = (s * 16807) % 2147483647) / 2147483647); }

// ---------------------------------------------------------------- tooltip
const tip = $("#tooltip");
function mostrarTip(evento, html) {
  tip.innerHTML = html; tip.hidden = false;
  const x = Math.min(evento.clientX + 14, innerWidth - tip.offsetWidth - 8);
  tip.style.left = `${x}px`; tip.style.top = `${evento.clientY + 14}px`;
}
const ocultarTip = () => { tip.hidden = true; };
function conTip(nodo, html) {
  nodo.addEventListener("mousemove", (e) => mostrarTip(e, html));
  nodo.addEventListener("mouseleave", ocultarTip);
  nodo.addEventListener("focus", () => { const r = nodo.getBoundingClientRect(); mostrarTip({ clientX: r.right, clientY: r.top }, html); });
  nodo.addEventListener("blur", ocultarTip);
}

// ---------------------------------------------------------------- estado general
let ESTADO = null;

const textoDataset = (d, v) => {
  if (d.tipo === "ninguno") return "Sin datos reales todavía";
  if (d.tipo === "demo") return `${fmt.format(d.avisos)} avisos de demostración`;
  const cuando = v && v.ultima_verificacion ? `, verificados ${haceCuanto(v.ultima_verificacion)}` : "";
  return `${fmt.format(d.avisos)} avisos reales${cuando}`;
};

// ---------------------------------------------------------------- trazabilidad de cada aviso
const NOMBRE_PORTAL = { fincaraiz: "Finca Raíz", ciencuadras: "Ciencuadras", metrocuadrado: "Metrocuadrado" };
const fechaLarga = (iso) => new Date(iso).toLocaleString("es-CO", { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" });
const diaLargo = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString("es-CO", { day: "numeric", month: "short", year: "numeric" });

function haceCuanto(iso) {
  if (!iso) return "sin fecha";
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (min < 2) return "hace un momento";
  if (min < 60) return `hace ${min} min`;
  const h = Math.round(min / 60);
  if (h < 24) return `hace ${h} h`;
  const d = Math.round(h / 24);
  return d === 1 ? "hace 1 día" : `hace ${d} días`;
}

// "Finca Raíz · verificado hace 2 h", con el detalle completo al pasar el puntero.
function celdaFuente(a, corta = false) {
  const f = a.fuente || {};
  const portal = esc(NOMBRE_PORTAL[f.portal] || f.portal || "Fuente desconocida");
  let estado, clase = "";
  if (f.disponible === false) { estado = "retirado del portal"; clase = " fuente--retirado"; }
  else if (!f.verificado_en) { estado = "sin verificar"; clase = " fuente--vieja"; }
  else if (!f.vigente) { estado = `sin verificar desde ${diaLargo(f.verificado_en.slice(0, 10))}`; clase = " fuente--vieja"; }
  else estado = `verificado ${haceCuanto(f.verificado_en)}`;
  const detalle = [
    `Publicado: ${f.publicado ? diaLargo(f.publicado) : "el portal no informa la fecha"}`,
    `Visto por primera vez: ${f.primera_vez ? fechaLarga(f.primera_vez) : "—"}`,
    `Última verificación con la fuente: ${f.verificado_en ? fechaLarga(f.verificado_en) : "—"}`,
    `Último cambio de datos: ${f.actualizado_en ? fechaLarga(f.actualizado_en) : "—"}`,
  ].join("\n");
  return `<span class="fuente${clase}" title="${esc(detalle)}">${corta ? "" : portal}<small>${esc(estado)}</small>${
    corta || !f.publicado ? "" : `<small>publicado ${esc(diaLargo(f.publicado))}</small>`}</span>`;
}

function pintarDataset() {
  const d = ESTADO.dataset;
  $("#insignia-texto").textContent = textoDataset(d, ESTADO.verificacion);
  $("#aviso-demo").hidden = d.tipo !== "demo";
}

// Sin avisos reales suficientes: no se muestra nada calculado, solo el avance de la recolección.
function modoSinDatos() {
  REG.warn("Sin datos reales suficientes: la página muestra solo el avance de la recolección", ESTADO.dataset);
  document.body.classList.add("modo-sin-datos");
  $("#sin-datos").hidden = false;
  $("#sd-motivo").textContent = ESTADO.dataset.motivo || "La recolección automática está reuniendo los primeros avisos.";
}

function avisoEnSeccion(selector, que) {
  return (e) => {
    const nodo = $(selector);
    if (nodo) nodo.innerHTML = `<p class="error-seccion" role="alert">No se pudo cargar ${esc(que)}: ${esc(e.message)}. Los detalles quedaron en el registro de la aplicación.</p>`;
  };
}

async function iniciar() {
  const t = REG.tiempo("Arranque de la página");
  REG.debug("Leaflet disponible", { leaflet: Boolean(window.L), version: window.L?.version });
  if (!window.L) REG.error("Leaflet (mapas) no cargó: los mapas no se verán. Revise /static/vendor/leaflet/leaflet.js");
  ESTADO = await api("/api/estado");
  pintarDataset();
  if (ESTADO.dataset.tipo === "ninguno") {
    modoSinDatos();
    cargarActualizacion();
    t.fin();
    return;
  }
  REG.info(`Datos: ${ESTADO.dataset.archivo} (${ESTADO.dataset.tipo}), ${ESTADO.dataset.avisos} avisos; modelo ${ESTADO.modelo.nombre}`, ESTADO.verificacion);
  for (const sel of [$("#localidad"), $("#f-localidad")]) {
    for (const l of ESTADO.localidades) sel.add(new Option(l, l));
  }
  construirFormulario(ESTADO.referencia);
  REG.debug("Formulario listo con valores de la URL", leerFormulario());
  cargarPerfil($("#localidad").value || null);
  estimarAhora();
  seguro("Cargar mercado", cargarMercado, avisoEnSeccion("#mercado .contenedor", "el análisis de mercado"));
  seguro("Cargar zonas", cargarZonas, avisoEnSeccion("#z-panel", "el análisis por zonas"));
  seguro("Cargar avisos", cargarAvisos, avisoEnSeccion("#a-filas", "la lista de avisos"));
  seguro("Cargar modelo", cargarModelo, avisoEnSeccion("#modelo .contenedor", "la información del modelo"));
  cargarActualizacion();
  t.fin();
}

// Cuando el ciclo automático publica un dataset nuevo, toda la página se pone al día sin recargar.
async function refrescarTodo() {
  REG.info("El ciclo automático publicó datos nuevos: se actualiza toda la página");
  ESTADO = await api("/api/estado");
  pintarDataset();
  ZONAS.cache.clear();
  await Promise.all([cargarMercado(), cargarZonas(), cargarAvisos(), cargarModelo()]);
  estimarAhora();
}

// ---------------------------------------------------------------- formulario
let estratoElegido = 4;

// La estimación vive en la URL (?area=70&estrato=4…): se puede guardar, compartir y volver a ella.
const CAMPOS_URL = ["area", "estrato", "localidad", "habitaciones", "banos", "parqueaderos", "antiguedad", "piso", "administracion"];
let interactuo = false;   // false = el precio en pantalla es el ejemplo inicial, no el apartamento del usuario

function leerURL() {
  const q = new URLSearchParams(location.search);
  const entero = (k, lo, hi) => { const v = parseInt(q.get(k), 10); return Number.isFinite(v) && v >= lo && v <= hi ? v : null; };
  const d = {
    area: entero("area", 15, 1000), estrato: entero("estrato", 1, 6), habitaciones: entero("habitaciones", 0, 8),
    banos: entero("banos", 0, 8), parqueaderos: entero("parqueaderos", 0, 6), antiguedad: entero("antiguedad", 0, 100),
    piso: entero("piso", 1, 60), administracion: entero("administracion", 0, 10000000),
    localidad: ESTADO.localidades.includes(q.get("localidad")) ? q.get("localidad") : null,
  };
  if (d.area != null) { $("#area").value = d.area; $("#area-rango").value = d.area; }
  if (d.localidad) $("#localidad").value = d.localidad;
  if (d.antiguedad != null) $("#antiguedad").value = d.antiguedad;
  if (d.piso != null) $("#piso").value = d.piso;
  if (d.administracion != null) $("#administracion").value = d.administracion;
  if (d.piso != null || d.administracion != null) $("#mas-detalles").open = true;
  for (const n of ["habitaciones", "banos", "parqueaderos"]) {
    if (d[n] != null) document.querySelector(`.contador[data-nombre=${n}]`).dataset.valor = d[n];
  }
  interactuo = CAMPOS_URL.some((k) => d[k] != null);
  return d.estrato;
}

function escribirURL(datos) {
  const q = new URLSearchParams();
  const valores = { ...datos, area: datos.area_m2 };
  for (const k of CAMPOS_URL) if (valores[k] != null && valores[k] !== "") q.set(k, valores[k]);
  history.replaceState(null, "", `?${q}${location.hash}`);
}

function construirFormulario(ref) {
  const grupo = $("#estratos");
  const estratoURL = leerURL();
  estratoElegido = estratoURL ?? (Math.round(parseFloat(ref.estrato)) || 4);
  for (let e = 1; e <= 6; e++) {
    const b = document.createElement("button");
    b.type = "button"; b.innerHTML = `${e}<small class="segmento__n"></small>`; b.setAttribute("role", "radio");
    b.setAttribute("aria-label", `Estrato ${e}`);
    b.style.setProperty("--c", COLOR_ESTRATO[e]);
    b.setAttribute("aria-checked", String(e === estratoElegido));
    b.tabIndex = e === estratoElegido ? 0 : -1;
    b.addEventListener("click", () => elegirEstrato(e, true));
    b.addEventListener("keydown", (ev) => {
      const paso = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1 }[ev.key];
      if (paso) { ev.preventDefault(); elegirEstrato(Math.min(6, Math.max(1, estratoElegido + paso)), true); }
    });
    grupo.appendChild(b);
  }

  document.querySelectorAll(".contador").forEach((c) => {
    const min = +c.dataset.min, max = +c.dataset.max;
    let valor = +c.dataset.valor;
    const etiqueta = c.querySelector(".contador__etiqueta");
    const control = document.createElement("div");
    control.className = "contador__control";
    control.setAttribute("role", "group");
    control.setAttribute("aria-labelledby", etiqueta.id);
    control.innerHTML = `<button type="button" aria-label="Menos ${etiqueta.textContent.toLowerCase()}">−</button>
      <output aria-live="polite"></output>
      <button type="button" aria-label="Más ${etiqueta.textContent.toLowerCase()}">+</button>`;
    etiqueta.after(control);      // etiqueta, control y debajo la pista de la zona
    const [menos, salida, mas] = control.children;
    const pintar = () => { salida.textContent = valor; menos.disabled = valor <= min; mas.disabled = valor >= max; c.dataset.valor = valor; };
    c.fijar = (v) => { valor = Math.min(max, Math.max(min, v)); pintar(); };
    menos.addEventListener("click", () => { valor = Math.max(min, valor - 1); pintar(); programar(); });
    mas.addEventListener("click", () => { valor = Math.min(max, valor + 1); pintar(); programar(); });
    pintar();
  });

  const caja = $("#area"), rango = $("#area-rango");
  caja.addEventListener("input", () => { rango.value = caja.value; programar(); });
  rango.addEventListener("input", () => { caja.value = rango.value; programar(); });
  $("#localidad").addEventListener("change", () => { cargarPerfil($("#localidad").value || null); programar(); });
  $("#antiguedad").addEventListener("input", programar);
  $("#piso").addEventListener("input", programar);
  $("#administracion").addEventListener("input", programar);
  $("#formulario").addEventListener("submit", (e) => { e.preventDefault(); estimarAhora(); });
}

function elegirEstrato(e, enfocar) {
  estratoElegido = e;
  document.querySelectorAll("#estratos button").forEach((b, i) => {
    const activo = i + 1 === e;
    b.setAttribute("aria-checked", String(activo)); b.tabIndex = activo ? 0 : -1;
    if (activo && enfocar) b.focus();
  });
  programar();
}

// ---------------------------------------------------------------- perfil real de la zona
// Antes de estimar se muestra qué tipos de apartamento existen de verdad en la localidad elegida,
// y el formulario se adapta: cuántos avisos hay de cada estrato, lo común en habitaciones, baños y
// parqueaderos, y el rango de áreas que se publica allí.
const PERFILES = new Map();
let perfilActual = null;

async function cargarPerfil(localidad) {
  const clave = localidad || "";
  let p = PERFILES.get(clave);
  if (!p) {
    try { p = await api(`/api/perfil${localidad ? `?localidad=${encodeURIComponent(localidad)}` : ""}`); }
    catch (e) { REG.warn(`No se pudo leer el perfil de ${localidad || "Bogotá"}`, e.message); return; }
    PERFILES.set(clave, p);
  }
  if (($("#localidad").value || "") !== clave) return;     // el usuario ya eligió otra
  perfilActual = p;
  REG.debug(`Perfil de ${p.zona}: ${p.n} avisos`, p.comunes);
  pintarPerfil(p);
}

const pctTexto = (n, total) => `${fmt.format(Math.round((n / total) * 100))} %`;

function listaConteos(items, total, unidad) {
  if (!items || !items.length) return `<span class="pz-nada">sin datos</span>`;
  const max = Math.max(...items.map((i) => i.n));
  return items.map((i) => `<span class="pz-barra" title="${fmt.format(i.n)} avisos (${pctTexto(i.n, total)})">
      <b>${i.valor}${unidad}</b><i style="width:${Math.max(6, (i.n / max) * 100)}%"></i><em>${fmt.format(i.n)}</em></span>`).join("");
}

function pintarPerfil(p) {
  $("#pz-titulo").textContent = p.n
    ? `Lo que hay en ${p.zona}: ${fmt.format(p.n)} avisos reales`
    : `No hay avisos reales de ${p.zona} todavía`;
  $("#pz-resumen").textContent = p.n ? `Mediana ${corto(p.precio.mediana)} · m² ${m2(p.m2.mediana)} · ver detalle` : "";
  const cuerpo = $("#pz-cuerpo");
  const pistas = { estrato: $("#p-estrato"), habitaciones: $("#p-habitaciones"), banos: $("#p-banos"),
    parqueaderos: $("#p-parqueaderos"), area: $("#p-area") };
  document.querySelectorAll("#estratos button").forEach((b, i) => {
    const n = p.n ? (p.estratos.find((x) => x.valor === i + 1)?.n ?? 0) : null;
    b.querySelector(".segmento__n").textContent = n == null ? "" : fmt.format(n);
    b.classList.toggle("segmento--vacio", n === 0);
    b.setAttribute("aria-label", n == null ? `Estrato ${i + 1}` : `Estrato ${i + 1}, ${n} avisos en ${p.zona}`);
  });
  if (!p.n) {
    cuerpo.innerHTML = `<p>${esc(p.mensaje || "")} La estimación buscará comparables en localidades vecinas y lo dirá.</p>`;
    Object.values(pistas).forEach((x) => { x.textContent = ""; });
    return;
  }
  const comun = (c, unidad) => {
    const m = p.comunes[c];
    return m ? `Común: ${m.valor}${unidad} (${fmt.format(Math.round(m.porcentaje * 100))} %)` : "";
  };
  pistas.estrato.textContent = `· el número es cuántos avisos hay en ${p.zona}`;
  pistas.habitaciones.textContent = comun("habitaciones", "");
  pistas.banos.textContent = comun("banos", "");
  pistas.parqueaderos.textContent = comun("parqueaderos", "");
  pistas.area.textContent = `Aquí se publican de ${fmt.format(Math.round(p.area.min))} a ${fmt.format(Math.round(p.area.max))} m² (mitad bajo ${fmt.format(Math.round(p.area.mediana))} m²).`;
  const tramosArea = p.area.tramos.map((t) => ({ valor: t.hasta ? `${t.desde}–${t.hasta}` : `${t.desde}+`, n: t.n }));
  const tramosAnt = p.antiguedad.tramos.map((t) => ({ valor: t.tramo, n: t.n }));
  const sinDato = (c) => (p.sin_dato[c] ? ` <small>(${fmt.format(p.sin_dato[c])} sin dato)</small>` : "");
  cuerpo.innerHTML = `
    <dl class="pz-cifras">
      <div><dt>Precio mediano</dt><dd>${corto(p.precio.mediana)}</dd></div>
      <div><dt>Precio promedio</dt><dd>${corto(p.precio.promedio)}</dd></div>
      <div><dt>m² promedio</dt><dd>${m2(p.m2.promedio)}</dd></div>
      <div><dt>Precios del 80 % central</dt><dd>${corto(p.precio.p10)} a ${corto(p.precio.p90)}</dd></div>
    </dl>
    <div class="pz-grupo"><h3>Estrato${sinDato("estrato")}</h3>${listaConteos(p.estratos, p.n, "")}</div>
    <div class="pz-grupo"><h3>Habitaciones${sinDato("habitaciones")}</h3>${listaConteos(p.habitaciones, p.n, "")}</div>
    <div class="pz-grupo"><h3>Baños${sinDato("banos")}</h3>${listaConteos(p.banos, p.n, "")}</div>
    <div class="pz-grupo"><h3>Parqueaderos${sinDato("parqueaderos")}</h3>${listaConteos(p.parqueaderos, p.n, "")}</div>
    <div class="pz-grupo"><h3>Área (m²)</h3>${listaConteos(tramosArea, p.n, "")}</div>
    <div class="pz-grupo"><h3>Antigüedad <small>(informada en ${fmt.format(p.antiguedad.informados)} de ${fmt.format(p.n)})</small></h3>${listaConteos(tramosAnt, p.antiguedad.informados || 1, "")}</div>
    ${p.tipos.length ? `<div class="pz-grupo pz-tipos"><h3>Tipos más publicados</h3><ul>${p.tipos.map((t) =>
      `<li><button type="button" class="pz-tipo" data-tipo='${esc(JSON.stringify(t))}'>Estrato ${t.estrato}, ${t.habitaciones} hab.${t.banos != null ? `, ${t.banos} baños` : ""}${t.parqueaderos != null ? `, ${t.parqueaderos} parq.` : ""}</button> <em>${fmt.format(t.n)} avisos</em></li>`).join("")}</ul>
      <p class="como-leer">Toque un tipo para estimarlo.</p></div>` : ""}`;
  cuerpo.querySelectorAll(".pz-tipo").forEach((b) => b.addEventListener("click", () => {
    const t = JSON.parse(b.dataset.tipo);
    REG.info("Tipo de la zona elegido desde el perfil", t);
    for (const n of ["habitaciones", "banos", "parqueaderos"]) {
      if (t[n] != null) document.querySelector(`.contador[data-nombre=${n}]`).fijar(t[n]);
    }
    elegirEstrato(t.estrato, false);
  }));
}

let temporizador;
function programar() { interactuo = true; clearTimeout(temporizador); temporizador = setTimeout(estimarAhora, 350); }

function leerFormulario() {
  const num = (v) => (v === "" || v == null ? null : Number(v));
  const contador = (n) => Number(document.querySelector(`.contador[data-nombre=${n}]`).dataset.valor);
  return {
    area_m2: num($("#area").value), estrato: estratoElegido,
    localidad: $("#localidad").value || null,
    habitaciones: contador("habitaciones"), banos: contador("banos"), parqueaderos: contador("parqueaderos"),
    antiguedad: num($("#antiguedad").value),
    piso: num($("#piso").value), administracion: num($("#administracion").value),
    ...ubicacionElegida($("#localidad").value || null),
  };
}

// Punto que se usa para el m² de los vecinos: el que el usuario tocó en el mapa (si sigue en la misma
// localidad) o el centro aproximado de la localidad. Sin localidad, ninguno.
function ubicacionElegida(localidad) {
  if (typeof MINI !== "undefined" && MINI.punto && MINI.nombrePunto === localidad) {
    return { latitud: +MINI.punto.lat.toFixed(5), longitud: +MINI.punto.lng.toFixed(5) };
  }
  const c = localidad && CENTROS[localidad];
  return c ? { latitud: c[0], longitud: c[1] } : {};
}

let ultimaPeticion = 0;
async function estimarAhora() {
  const datos = leerFormulario();
  const error = $("#error-form");
  if (!datos.area_m2 || datos.area_m2 < 15 || datos.area_m2 > 1000) {
    REG.debug("Estimación no enviada: área fuera de rango", datos);
    error.textContent = "Escriba un área entre 15 y 1.000 m²."; error.hidden = false; return;
  }
  error.hidden = true;
  const form = $("#formulario"); form.classList.add("calculando");
  const id = ++ultimaPeticion;
  try {
    const r = await api("/api/estimar", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(datos) });
    if (id !== ultimaPeticion) { REG.debug(`Estimación ${id} descartada: llegó una más nueva (${ultimaPeticion})`); return; }
    REG.debug(r.precio == null
      ? `Sin estimación: no hay comparables para ${datos.area_m2} m², estrato ${datos.estrato}, ${datos.localidad || "sin localidad"}`
      : `Estimación: ${datos.area_m2} m², estrato ${datos.estrato}, ${datos.localidad || "sin localidad"} -> ${corto(r.precio)} (${corto(r.minimo)}–${corto(r.maximo)}), ${r.comparables.usados} comparables, confiabilidad ${r.comparables.confiabilidad}`);
    pintarResultado(r, datos);
  } catch (e) {
    REG.error("Falló la estimación", { mensaje: e.message, datos, pila: e.stack });
    error.textContent = `No se pudo estimar: ${e.message}`; error.hidden = false;
  } finally { if (id === ultimaPeticion) form.classList.remove("calculando"); }
}

// ---------------------------------------------------------------- resultado
let primeraVez = true;

const NIVEL = { alta: "Confiabilidad alta", media: "Confiabilidad media", baja: "Confiabilidad baja", insuficiente: "Sin información suficiente" };

function pintarViabilidad(r, datos) {
  const c = r.comparables, caja = $("#r-viabilidad");
  caja.dataset.nivel = c.confiabilidad;
  const zona = c.zona;
  const encontrados = c.viable
    ? `Encontré ${fmt.format(c.encontrados)} apartamento${c.encontrados === 1 ? "" : "s"} ${esc(c.criterio)}; uso ${c.usados === c.encontrados ? "todos" : `los ${fmt.format(c.usados)} más parecidos`}.`
    : `Revisé ${fmt.format(c.avisos_en_zona)} avisos reales de ${esc(zona)} y amplié la búsqueda paso a paso, sin encontrar apartamentos comparables.`;
  caja.innerHTML = `<p class="viabilidad__nivel"><span class="semaforo" aria-hidden="true"></span>${NIVEL[c.confiabilidad]}</p>
    <p>${encontrados}</p>
    ${c.amplio && c.viable ? `<p class="viabilidad__aviso">No había suficientes coincidencias exactas: la búsqueda se amplió (detalle abajo, en "Cómo se obtuvo").</p>` : ""}
    ${c.viable ? "" : `<p>${esc(c.razones[0])}</p><p>Pruebe con características más comunes en ${esc(zona)} (vea "Lo que hay en ${esc(zona)}" en el formulario) o sin localidad.</p>`}`;
}

function mezclaTexto(c, r) {
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

function pintarExplicacion(r, datos) {
  const c = r.comparables, caja = $("#r-explicacion");
  const pasos = c.pasos.map((p, i) => `<li${p.clave === c.paso ? ' class="elegido"' : ""}><span>${i + 1}. Buscar ${esc(p.criterio)}</span><b>${fmt.format(p.encontrados)}</b></li>`).join("");
  if (!c.viable) {
    caja.innerHTML = `<p>Búsquedas hechas, de la más estricta a la más amplia, y cuántos apartamentos reales encontró cada una:</p><ol class="pasos">${pasos}</ol>
      <p>Ninguna encontró apartamentos comparables, así que no se calcula un precio.</p>`;
    return;
  }
  const origen = c.origen.localidades.map((o) => `${esc(o.localidad)} (${o.n})`).join(", ");
  const barrios = c.origen.barrios.length ? c.origen.barrios.map((o) => `${esc(o.barrio)} (${o.n})`).join(", ") : "los avisos no informan barrio";
  caja.innerHTML = `
    <dl class="explica">
      <div><dt>Apartamentos encontrados</dt><dd>${fmt.format(c.encontrados)}</dd></div>
      <div><dt>Usados como referencia</dt><dd>${fmt.format(c.usados)}</dd></div>
      <div><dt>Precio promedio de los comparables</dt><dd>${corto(c.promedio_precio)}</dd></div>
      <div><dt>m² promedio · mediano</dt><dd>${m2(c.promedio_m2)} · ${m2(c.mediana_m2)}</dd></div>
      <div><dt>Rango razonable</dt><dd>${corto(r.minimo)} a ${corto(r.maximo)}</dd></div>
      <div><dt>Confiabilidad</dt><dd>${NIVEL[c.confiabilidad].replace("Confiabilidad ", "")}</dd></div>
    </dl>
    <h3>Cómo se calculó</h3>
    ${mezclaTexto(c, r)}
    <p>Los comparables solos darían ${corto(c.precio)}: m² mediano de los ${fmt.format(c.usados)} (${m2(c.mediana_m2)}) × ${fmt.format(datos.area_m2)} m². ${c.rango_ampliado
      ? `Con solo ${c.usados} comparable${c.usados === 1 ? "" : "s"} su dispersión no mide bien la incertidumbre, así que el rango se amplió al margen de error del modelo (calibrado para acertar 8 de cada 10 veces).`
      : `El rango usa la mitad central de sus precios por m²${c.usados < 5 ? " (con tan pocos, todo lo observado)" : ""}.`}</p>
    <h3>Características tenidas en cuenta</h3><p>${esc(c.considerados.join(", "))}.</p>
    <h3>Búsquedas, de la más estricta a la más amplia</h3><ol class="pasos">${pasos}</ol>
    <h3>De dónde vienen</h3><p>Localidades: ${origen}. Barrios: ${barrios}.</p>
    <h3>Diferencias con su apartamento</h3><ul class="diferencias">${c.diferencias.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>
    <h3>Por qué esta confiabilidad</h3><ul class="diferencias">${c.razones.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>`;
}

function pintarResultado(r, datos) {
  $("#resultado-vacio").hidden = true;
  $("#resultado-lleno").hidden = false;
  $("#detalle").hidden = false;
  $("#resultado").setAttribute("aria-busy", "false");
  pintarViabilidad(r, datos);
  pintarExplicacion(r, datos);
  const conPrecio = r.precio != null;
  $("#r-con-precio").hidden = !conPrecio;
  document.querySelector("#detalle .efectos").hidden = !conPrecio;
  document.querySelector("#detalle .parecidos").hidden = !conPrecio;
  if (interactuo) escribirURL(datos);
  if (!conPrecio) {
    $("#pf-precio").textContent = "Sin información suficiente";
    hayResultado = true; actualizarFlotante();
    $("#r-advertencias").replaceChildren();
    ubicarEstimacion(r, datos);
    return;
  }
  const mo = r.modelo;
  $("#r-contraste").textContent = mo.diferencia_pct == null ? "" : Math.abs(mo.diferencia_pct) < 0.05
    ? `El modelo (${mo.nombre}) da casi lo mismo: ${corto(mo.precio)}. Dos métodos distintos coinciden.`
    : `El modelo (${mo.nombre}), entrenado con todos los avisos, da ${corto(mo.precio)}: ${fmt.format(Math.abs(Math.round(mo.diferencia_pct * 100)))} % ${mo.diferencia_pct > 0 ? "más" : "menos"} que los comparables. Cuando difieren mucho, conviene mirar los comparables uno por uno.`;
  const rotulo = $("#r-rotulo");
  rotulo.textContent = interactuo ? "Precio de publicación estimado" : "Ejemplo con un apartamento típico: cambie los datos para ver el suyo";
  rotulo.classList.toggle("resultado__rotulo--ejemplo", !interactuo);
  $("#r-precio").textContent = millones(r.precio);
  $("#pf-precio").textContent = millones(r.precio);
  hayResultado = true; actualizarFlotante();
  $("#r-rango").textContent = `Lo más probable es que un aviso así pida entre ${corto(r.minimo)} y ${corto(r.maximo)}.`;
  $("#r-m2").textContent = m2(r.precio_m2);
  const c = r.contexto;
  $("#r-contexto").textContent = c.estrato_avisos
    ? `Es más alto que el del ${c.percentil_m2} % de los ${fmt.format(c.estrato_avisos)} avisos de estrato ${datos.estrato}, cuya mediana es ${m2(c.estrato_mediana_m2)}.`
    : `No hay avisos de estrato ${datos.estrato} para comparar.`;

  const zona = $("#r-zona");
  zona.hidden = !r.zona;
  if (r.zona) {
    const d = r.zona.diferencia_m2_pct;
    zona.textContent = Math.abs(d) < 0.02
      ? `En ${r.zona.localidad}, el metro cuadrado estimado está en la mediana de la localidad (${m2(r.zona.mediana_m2)}).`
      : `En ${r.zona.localidad}, el metro cuadrado estimado es ${fmt.format(Math.abs(Math.round(d * 100)))} % ${d > 0 ? "más alto" : "más bajo"} que la mediana de la localidad (${m2(r.zona.mediana_m2)}).`;
  }

  const adv = $("#r-advertencias"); adv.replaceChildren();
  if (r.demo) adv.insertAdjacentHTML("beforeend", "<li>Estimación hecha con datos de demostración: sirve para probar la aplicación, no como referencia de precio.</li>");
  for (const a of r.advertencias) { const li = document.createElement("li"); li.textContent = a; adv.appendChild(li); }

  pintarCinta(r);
  pintarEfectos(r);
  pintarSimilares(r.similares);
  ubicarEstimacion(r, datos);
}

function pintarCinta(r) {
  const svg = $("#cinta"); svg.replaceChildren();
  const W = 600, y0 = 72;
  const valores = [r.minimo, r.maximo, r.precio, ...r.similares.map((s) => s.precio)];
  let lo = Math.min(...valores), hi = Math.max(...valores);
  const margen = (hi - lo) * 0.08 || r.precio * 0.1; lo -= margen; hi += margen;
  const x = (v) => 10 + ((v - lo) / (hi - lo)) * (W - 20);

  el("rect", { class: "franja", x: x(r.minimo), y: y0 - 30, width: x(r.maximo) - x(r.minimo), height: 30, rx: 3 }, svg);
  el("line", { class: "regla", x1: 10, x2: W - 10, y1: y0, y2: y0 }, svg);
  // marcas de cinta métrica: largas en los valores redondos, cortas entre ellas
  const marcas = marcasBonitas(lo, hi, 6);
  const paso = marcas.length > 1 ? (marcas[1] - marcas[0]) / 5 : 0;
  if (paso) for (let v = marcas[0] - 5 * paso; v <= hi; v += paso) {
    if (v < lo) continue;
    el("line", { class: "marca-m", x1: x(v), x2: x(v), y1: y0, y2: y0 + 5, "stroke-width": 1 }, svg);
  }
  for (const v of marcas) {
    el("line", { class: "marca-m", x1: x(v), x2: x(v), y1: y0, y2: y0 + 12, "stroke-width": 1.5 }, svg);
    const t = el("text", { class: "marca-t", x: x(v), y: y0 + 28, "text-anchor": "middle" }, svg);
    t.textContent = corto(v);
  }
  // avisos parecidos, apilados si se tocan
  const ocupados = [];
  for (const s of r.similares) {
    const cx = x(s.precio);
    let fila = 0;
    while (ocupados.some((o) => o.fila === fila && Math.abs(o.cx - cx) < 13)) fila++;
    ocupados.push({ cx, fila });
    const c = el("circle", { class: "vecino", cx, cy: y0 - 12 - fila * 13, r: 5.5, tabindex: 0 }, svg);
    conTip(c, `${corto(s.precio)}, ${area(s.area_m2)}<br>Estrato ${esc(s.estrato ?? "sin dato")}${s.localidad ? `, ${esc(s.localidad)}` : ""}`);
  }
  // aguja del precio estimado: el único movimiento automático de la página
  const g = el("g", { class: "grupo-aguja" }, svg);
  el("line", { class: "aguja", x1: 0, x2: 0, y1: y0 - 48, y2: y0 + 2 }, g);
  el("path", { class: "aguja-cabeza", d: "M-7,-56 L7,-56 L0,-46 Z", transform: `translate(0 ${y0})` }, g);
  const destino = x(r.precio);
  if (primeraVez && !reducirMovimiento) {
    g.style.transform = `translateX(10px)`;
    requestAnimationFrame(() => requestAnimationFrame(() => { g.style.transform = `translateX(${destino}px)`; }));
  } else {
    g.style.transform = `translateX(${destino}px)`;
  }
  primeraVez = false;
  svg.setAttribute("aria-label", `Precio estimado ${millones(r.precio)}, rango de ${corto(r.minimo)} a ${corto(r.maximo)}.`);
}

function pintarEfectos(r) {
  const caja = $("#r-efectos"); caja.replaceChildren();
  const ref = r.referencia;
  $("#r-efectos-guia").textContent = `Comparado con el apartamento típico de los datos (${ref.area_m2}, estrato ${ref.estrato}${ref.localidad && ref.localidad !== "Sin dato" ? `, ${ref.localidad}` : ""}): cuánto cambia la estimación por cada característica distinta. Son aproximaciones; los efectos no se suman exactamente.`;
  if (!r.efectos.length) {
    caja.innerHTML = `<p class="efectos__vacio">Este apartamento se parece al típico, o el modelo (${esc(r.modelo.nombre)}) no usa las características en las que difiere.</p>`;
    return;
  }
  const maximo = Math.max(...r.efectos.map((e) => Math.abs(e.efecto)));
  for (const e of r.efectos) {
    const sube = e.efecto > 0;
    const fila = document.createElement("div"); fila.className = "efecto";
    const ancho = (Math.abs(e.efecto) / maximo) * 50;
    fila.innerHTML = `<span class="efecto__nombre">${esc(e.nombre)}: ${esc(e.valor)}<small>típico: ${esc(e.tipico)}</small></span>
      <span class="efecto__pista" aria-hidden="true"><span class="efecto__barra efecto__barra--${sube ? "sube" : "baja"}" style="width:${ancho}%"></span></span>
      <span class="efecto__valor">${sube ? "+" : "−"}${corto(Math.abs(e.efecto))}</span>`;
    caja.appendChild(fila);
  }
}

function enlaceAviso(url) {
  url = urlSegura(url);
  return url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Ver aviso<span class="sr"> (se abre en otra pestaña)</span></a>` : "";
}
function chipEstrato(e) {
  if (e == null) return "—";
  const oscuro = e >= 4;
  return `<span class="chip-estrato" style="background:${COLOR_ESTRATO[e]};color:${oscuro ? "#fff" : "var(--paramo)"}">${e}</span>`;
}

function pintarSimilares(lista) {
  $("#r-similares").innerHTML = lista.map((s) => `<tr>
    <td>${corto(s.precio)}</td><td>${m2(s.precio_m2)}</td><td>${area(s.area_m2)}</td><td>${chipEstrato(s.estrato)}</td>
    <td>${esc(s.localidad ?? "—")}</td><td>${celdaFuente(s)}</td><td>${enlaceAviso(s.url_origen)}</td></tr>`).join("");
}

// ---------------------------------------------------------------- mercado
async function cargarMercado() {
  const m = await api("/api/mercado");
  const r = m.resumen;
  $("#m-resumen").innerHTML = `La mitad de los ${fmt.format(r.avisos)} avisos pide menos de <strong>${millones(r.mediana_precio)}</strong>.
    El metro cuadrado mediano está en <strong>${m2(r.mediana_m2)}</strong> y el área mediana es de <strong>${area(r.mediana_area)}</strong>.`;
  pintarCajas(m.estratos);
  document.querySelectorAll(".nota-omitidos").forEach((n) => n.remove());
  if (m.estratos_omitidos.length) {
    const lista = m.estratos_omitidos.map((o) => `${o.estrato} (${o.n})`).join(", ");
    $("#g-cajas").insertAdjacentHTML("afterend", `<p class="como-leer nota-omitidos">No se dibuja caja para los estratos con menos de 3 avisos: ${lista}. Entre paréntesis, cuántos avisos hay.</p>`);
  }
  pintarDispersion(m.dispersion);
  MERCADO = m;
  if (!cargarMercado.conResize) {
    cargarMercado.conResize = true;
    let ultimoAncho = innerWidth, espera;
    addEventListener("resize", () => {
      if (innerWidth === ultimoAncho) return;
      ultimoAncho = innerWidth; clearTimeout(espera);
      espera = setTimeout(() => { pintarCajas(MERCADO.estratos); pintarDispersion(MERCADO.dispersion); }, 200);
    });
  }
}
let MERCADO = null;

function pintarCajas(estratos) {
  const svg = $("#g-cajas"); svg.replaceChildren();
  if (!estratos.length) { svg.outerHTML = `<p class="como-leer">No hay suficientes avisos por estrato para dibujar cajas.</p>`; return; }
  // se dibuja al ancho real del contenedor para que el texto conserve su tamaño en cualquier pantalla
  const W = Math.max(300, svg.parentElement.clientWidth), estrecho = W < 620;
  // el lector de pantalla recibe las cifras, no solo el título del gráfico
  svg.setAttribute("aria-label", `Precio mediano del metro cuadrado según estrato: ${estratos.map((e) => `estrato ${e.estrato}, ${m2(e.mediana)} con ${e.n} avisos`).join("; ")}.`);
  const H = estrecho ? 316 : 340, iz = estrecho ? 62 : 86, de = 8, ar = 16, ab = estrecho ? 64 : 48;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const todos = estratos.flatMap((e) => [e.min, e.max, ...e.atipicos]);
  const hi = Math.max(...todos) * 1.05, lo = 0;
  const y = (v) => H - ab - ((v - lo) / (hi - lo)) * (H - ab - ar);
  for (const v of marcasBonitas(lo, hi / 1e6, 5)) {
    el("line", { class: "guia", x1: iz, x2: W - de, y1: y(v * 1e6), y2: y(v * 1e6) }, svg);
    el("text", { x: iz - 8, y: y(v * 1e6) + 4, "text-anchor": "end" }, svg).textContent = `$${fmt1.format(v)} M`;
  }
  if (!estrecho) {
    const t = el("text", { class: "titulo-eje", x: 0, y: 0, transform: `translate(14 ${(H - ab) / 2}) rotate(-90)`, "text-anchor": "middle" }, svg);
    t.textContent = "Precio por m²";
  }
  const banda = (W - iz - de) / estratos.length, cw = Math.min(70, banda * 0.42);
  const rnd = azar(11);
  estratos.forEach((e, i) => {
    const cx = iz + banda * (i + 0.5);
    const color = COLOR_ESTRATO[e.estrato];
    for (const p of e.puntos) {
      el("circle", { class: "punto", cx: cx + cw * 0.62 + rnd() * cw * 0.55, cy: y(p), r: 2.6, fill: color, opacity: 0.8 }, svg);
    }
    el("line", { class: "bigote", x1: cx, x2: cx, y1: y(e.max), y2: y(e.q3) }, svg);
    el("line", { class: "bigote", x1: cx, x2: cx, y1: y(e.q1), y2: y(e.min) }, svg);
    el("line", { class: "bigote", x1: cx - cw / 4, x2: cx + cw / 4, y1: y(e.max), y2: y(e.max) }, svg);
    el("line", { class: "bigote", x1: cx - cw / 4, x2: cx + cw / 4, y1: y(e.min), y2: y(e.min) }, svg);
    const caja = el("rect", { class: "caja", x: cx - cw / 2, y: y(e.q3), width: cw, height: Math.max(1, y(e.q1) - y(e.q3)), fill: color, rx: 3, tabindex: 0 }, svg);
    conTip(caja, `Estrato ${e.estrato}, ${fmt.format(e.n)} avisos<br>Mediana ${m2(e.mediana)} por m²<br>Mitad central: ${m2(e.q1)} a ${m2(e.q3)}`);
    el("line", { class: "mediana", x1: cx - cw / 2, x2: cx + cw / 2, y1: y(e.mediana), y2: y(e.mediana), stroke: e.estrato >= 5 ? "#fff" : "var(--paramo)" }, svg);
    if (!estrecho) el("text", { class: "cifra-mediana", x: cx - cw / 2 - 6, y: y(e.mediana) + 4, "text-anchor": "end" }, svg).textContent = m2(e.mediana);
    for (const a of e.atipicos) el("circle", { cx, cy: y(a), r: 3, fill: "none", stroke: "var(--paramo)" }, svg);
    el("text", { x: cx, y: H - ab + 22, "text-anchor": "middle", class: "titulo-eje" }, svg).textContent = estrecho ? e.estrato : `Estrato ${e.estrato}`;
    el("text", { x: cx, y: H - ab + 38, "text-anchor": "middle" }, svg).textContent = estrecho ? `n=${fmt.format(e.n)}` : `${fmt.format(e.n)} avisos`;
  });
  if (estrecho) {
    el("text", { class: "titulo-eje", x: 4, y: 10 }, svg).textContent = "Precio por m²";
    el("text", { class: "titulo-eje", x: (W + iz) / 2, y: H - 2, "text-anchor": "middle" }, svg).textContent = "Estrato";
  }
}

function pintarDispersion(puntos) {
  const svg = $("#g-dispersion"); svg.replaceChildren();
  const W = Math.max(300, svg.parentElement.clientWidth), H = Math.min(400, Math.round(W * 0.72)), iz = 64, de = 12, ar = 12, ab = 44;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const p99 = (arr) => { const s = [...arr].sort((a, b) => a - b); return s[Math.floor(s.length * 0.99)] ?? ultimo(s); };
  const xmax = p99(puntos.map((p) => p.area)) * 1.05, ymax = p99(puntos.map((p) => p.precio)) * 1.05;
  const x = (v) => iz + (v / xmax) * (W - iz - de), y = (v) => H - ab - (v / ymax) * (H - ab - ar);
  for (const v of marcasBonitas(0, ymax / 1e6, 5)) {
    el("line", { class: "guia", x1: iz, x2: W - de, y1: y(v * 1e6), y2: y(v * 1e6) }, svg);
    el("text", { x: iz - 8, y: y(v * 1e6) + 4, "text-anchor": "end" }, svg).textContent = `$${fmt.format(v)} M`;
  }
  for (const v of marcasBonitas(0, xmax, 5)) {
    el("line", { class: "eje", x1: x(v), x2: x(v), y1: H - ab, y2: H - ab + 5 }, svg);
    el("text", { x: x(v), y: H - ab + 18, "text-anchor": "middle" }, svg).textContent = fmt.format(v);
  }
  el("line", { class: "eje", x1: iz, x2: W - de, y1: H - ab, y2: H - ab }, svg);
  el("text", { class: "titulo-eje", x: (W + iz) / 2, y: H - 6, "text-anchor": "middle" }, svg).textContent = "Área (m²)";
  const ordenados = [...puntos].sort((a, b) => (a.estrato ?? 0) - (b.estrato ?? 0));
  for (const p of ordenados) {
    if (p.area > xmax || p.precio > ymax) continue;
    el("circle", { class: "punto", cx: x(p.area), cy: y(p.precio), r: 3.4, fill: p.estrato ? COLOR_ESTRATO[p.estrato] : "#999", opacity: 0.85 }, svg);
  }
  $("#leyenda-estratos").innerHTML = [1, 2, 3, 4, 5, 6].map((e) => `<span><i style="background:${COLOR_ESTRATO[e]}"></i>Estrato ${e}</span>`).join("");
}

function colorM2(t) {
  // interpolación de verde claro a ladrillo pasando por musgo
  const a = [201, 216, 196], b = [79, 107, 91], c = [180, 71, 44];
  const [p, q, u] = t < 0.5 ? [a, b, t * 2] : [b, c, (t - 0.5) * 2];
  return `rgb(${p.map((v, i) => Math.round(v + (q[i] - v) * u)).join(",")})`;
}

// ---------------------------------------------------------------- comparación con lo esperado
const pct = (v) => `${v > 0 ? "+" : "−"}${fmt.format(Math.abs(Math.round(v * 100)))} %`;
const pctEntero = (v) => `${fmt.format(Math.round(v * 100))} %`;
const COLOR_CMP = { debajo: "#4A7E67", en_linea: "#A9B6AE", encima: "#B4472C" };
const TEXTO_CMP = { debajo: "por debajo de lo esperado", en_linea: "en línea con lo esperado", encima: "por encima de lo esperado" };

function chipComparacion(a) {
  if (a.diferencia_pct == null) return "—";
  const flecha = a.comparacion === "debajo" ? "▼ " : a.comparacion === "encima" ? "▲ " : "";
  const texto = a.comparacion === "en_linea" ? "En línea" : `${flecha}${fmt.format(Math.abs(Math.round(a.diferencia_pct * 100)))} %`;
  return `<span class="cmp cmp--${esc(a.comparacion)}" title="${esc(`${pct(a.diferencia_pct)}: ${TEXTO_CMP[a.comparacion] ?? ""}. Esperado: ${corto(a.precio_esperado)}`)}">${texto}</span>`;
}

function precioAnterior(a) {
  if (!a.precio_anterior || a.precio_anterior === a.precio) return "";
  return `<span class="bajo-precio">${a.precio < a.precio_anterior ? "bajó" : "subió"} de ${corto(a.precio_anterior)}</span>`;
}

// ---------------------------------------------------------------- zonas: mapa, panel y comparación
const ZONAS = { datos: null, porNombre: new Map(), mapa: null, capaLoc: null, capaPuntos: null, capaRegion: null,
  marcaBogota: null, limites: {}, seleccion: null, escala: [0, 1], cache: new Map(), geo: null };

// Se guarda la promesa, no solo el resultado: el mini mapa y el mapa de zonas piden los límites a la vez
// y así se descargan una sola vez.
function datosGeo() {
  if (!ZONAS.geoPromesa) ZONAS.geoPromesa = leerGeo().catch((e) => { ZONAS.geoPromesa = null; throw e; });
  return ZONAS.geoPromesa;
}

async function leerGeo() {
  if (!ZONAS.geo) {
    const leer = async (url) => {
      const r = await fetch(url);
      if (!r.ok) throw new Error(`${url} respondió ${r.status}`);
      return r.json();
    };
    const t = REG.tiempo("Leer límites del mapa");
    try {
      const [loc, reg] = await Promise.all([leer("/static/geo/localidades.geojson"), leer("/static/geo/region.geojson")]);
      ZONAS.geo = { loc, reg };
      t.fin({ localidades: loc.features?.length, region: reg.features?.length });
    } catch (e) {
      t.fallo(e);
      throw e;
    }
  }
  return ZONAS.geo;
}

async function cargarZonas() {
  const [z, geo] = await Promise.all([api("/api/zonas"), datosGeo()]);
  const puntos = MERCADO ? MERCADO.mapa : (await api("/api/mercado")).mapa;
  REG.debug(`Zonas: ${z.localidades.filter((l) => l.n).length} localidades con avisos, ${puntos.length} puntos en el mapa`);
  ZONAS.datos = z;
  ZONAS.porNombre = new Map(z.localidades.map((l) => [l.nombre, l]));
  const conDatos = z.localidades.filter((l) => l.n >= 3);
  const m2s = conDatos.map((l) => l.mediana_m2);
  ZONAS.escala = m2s.length ? [Math.min(...m2s), Math.max(...m2s)] : [0, 1];
  $("#z-escala-min").textContent = m2s.length ? m2(ZONAS.escala[0]) : "";
  $("#z-escala-max").textContent = m2s.length ? m2(ZONAS.escala[1]) : "";

  const estables = conDatos.filter((l) => l.n >= 10).sort((a, b) => b.mediana_m2 - a.mediana_m2);
  $("#z-entrada").textContent = estables.length >= 2
    ? `${estables[0].nombre} es la localidad con el metro cuadrado más caro (${m2(estables[0].mediana_m2)}) y ${ultimo(estables).nombre} la más barata (${m2(ultimo(estables).mediana_m2)}). Elija una localidad en el mapa o en la lista para ver su análisis.`
    : "Elija una localidad en el mapa o en la lista para ver cuánto se pide allí y cómo se compara con el resto de Bogotá.";

  const sel = $("#z-localidad");
  sel.length = 1;
  for (const l of [...z.localidades].sort((a, b) => a.nombre.localeCompare(b.nombre, "es"))) {
    const o = new Option(l.n ? `${l.nombre} (${fmt.format(l.n)} avisos)` : `${l.nombre} (sin avisos)`, l.nombre);
    sel.add(o);
  }
  if (ZONAS.seleccion) sel.value = ZONAS.seleccion;
  pintarRanking();
  pintarMapaZonas(geo, puntos);
  if (ZONAS.seleccion) seleccionarZona(ZONAS.seleccion, { encuadrar: false });
}

function colorZona(l) {
  if (!l || l.n < 3) return null;
  const [lo, hi] = ZONAS.escala;
  return colorM2(hi > lo ? (l.mediana_m2 - lo) / (hi - lo) : 0.5);
}

function estiloLocalidad(f) {
  const l = ZONAS.porNombre.get(f.properties.nombre);
  const color = colorZona(l);
  const elegida = f.properties.nombre === ZONAS.seleccion;
  return {
    fillColor: color ?? "#DCE2DE", fillOpacity: color ? 0.62 : 0.45,
    color: elegida ? "#17322C" : "#FFFFFF", weight: elegida ? 3 : 1.2, dashArray: color ? null : "4 3",
  };
}

function pintarMapaZonas(geo, puntos) {
  const caja = $("#mapa-zonas");
  if (!window.L) {
    if (!pintarMapaZonas.reintento) {
      pintarMapaZonas.reintento = true;
      addEventListener("load", () => pintarMapaZonas(geo, puntos));
      return;
    }
    caja.innerHTML = `<div class="mapa__falla">No se pudo cargar el mapa. Revise que la carpeta app/static/vendor esté completa.</div>`;
    return;
  }
  if (!ZONAS.mapa) {
    const mapa = L.map(caja, { scrollWheelZoom: false, zoomSnap: 0.25, minZoom: 4, maxZoom: 17 });
    ZONAS.mapa = mapa;
    // Calles de fondo (necesitan internet). Si no cargan, los límites siguen viéndose: son datos propios.
    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a> | Localidades: IDECA (CC BY 4.0) | Países: Natural Earth',
      maxZoom: 18,
    }).on("tileerror", tilesFallidas("mapa de zonas")).addTo(mapa);
    REG.debug("Mapa de zonas creado");
    ZONAS.capaRegion = L.geoJSON(geo.reg, {
      interactive: false,
      style: (f) => (f.properties.nombre === "Colombia"
        ? { fillColor: "#17322C", fillOpacity: 0.1, color: "#17322C", weight: 1.4 }
        : { fillColor: "#7C9486", fillOpacity: 0.04, color: "#7C9486", weight: 0.8, dashArray: "3 3" }),
    }).addTo(mapa);
    const colombia = geo.reg.features.find((f) => f.properties.nombre === "Colombia");
    ZONAS.limites.colombia = L.geoJSON(colombia).getBounds();
    ZONAS.marcaBogota = L.circleMarker([4.65, -74.09], { radius: 7, color: "#fff", weight: 2, fillColor: "#B4472C", fillOpacity: 1 })
      .bindTooltip("Bogotá D.C.", { permanent: true, direction: "right", className: "etiqueta-loc etiqueta-bogota", offset: [8, 0] })
      .on("click", () => encuadrar("bogota"));
    ZONAS.capaPuntos = L.layerGroup();
    mapa.on("zoomend", visibilidadPorZoom);
    document.querySelectorAll(".vistas button").forEach((b) => b.addEventListener("click", () => encuadrar(b.dataset.vista)));
    $("#z-puntos").addEventListener("change", (e) => (e.target.checked ? ZONAS.capaPuntos.addTo(mapa) : ZONAS.capaPuntos.remove()));
  }
  const mapa = ZONAS.mapa;
  if (ZONAS.capaLoc) ZONAS.capaLoc.remove();
  ZONAS.capaLoc = L.geoJSON(geo.loc, {
    style: estiloLocalidad,
    onEachFeature: (f, capa) => {
      const nombre = f.properties.nombre;
      capa.bindTooltip(() => textoZona(nombre), { sticky: true, className: "etiqueta-loc" });
      capa.on({
        mouseover: () => { if (nombre !== ZONAS.seleccion) capa.setStyle({ weight: 2.5, color: "#17322C" }); },
        mouseout: () => ZONAS.capaLoc.resetStyle(capa),
        click: () => seleccionarZona(nombre, { encuadrar: true }),
      });
    },
  }).addTo(mapa);
  // Encuadre de la ciudad construida: Usme, Ciudad Bolívar y Sumapaz tienen grandes zonas rurales
  // que, incluidas, dejarían la ciudad diminuta.
  ZONAS.limites.bogota = L.latLngBounds([4.47, -74.215], [4.83, -74.01]);

  ZONAS.capaPuntos.clearLayers();
  const lienzo = L.canvas({ padding: 0.3 });
  for (const p of puntos) {
    L.circleMarker([p.lat, p.lon], { renderer: lienzo, radius: 3.6, weight: 0.8, color: "#fff",
      fillColor: COLOR_CMP[p.comparacion] ?? "#A9B6AE", fillOpacity: 0.95 })
      .bindPopup(() => popupAviso(p)).addTo(ZONAS.capaPuntos);
  }
  if ($("#z-puntos").checked) ZONAS.capaPuntos.addTo(mapa);
  if (!pintarMapaZonas.encuadrado) { pintarMapaZonas.encuadrado = true; encuadrar("bogota"); }
  visibilidadPorZoom();
  if (MINI.ultimo) marcarEnZonas(MINI.ultimo.pos, MINI.ultimo.r, MINI.ultimo.datos);   // la estimación llegó antes que el mapa
}

function textoZona(nombre) {
  const l = ZONAS.porNombre.get(nombre);
  if (!l || !l.n) return `<strong>${esc(nombre)}</strong><br>Sin avisos en los datos`;
  if (l.n < 3) return `<strong>${esc(nombre)}</strong><br>${l.n} aviso${l.n > 1 ? "s" : ""}: muy pocos para resumir`;
  return `<strong>${esc(nombre)}</strong><br>${m2(l.mediana_m2)} por m², ${fmt.format(l.n)} avisos`;
}

function popupAviso(p) {
  const url = urlSegura(p.url);
  const lugar = [p.barrio, p.localidad].filter(Boolean).map(esc).join(", ");
  return `<strong>${corto(p.precio)}</strong> ${chipComparacion({ ...p, precio_esperado: p.precio / (1 + p.diferencia_pct) })}<br>
    ${area(p.area)}, ${m2(p.m2)} por m²${p.estrato ? `, estrato ${p.estrato}` : ""}${lugar ? `<br>${lugar}` : ""}
    ${url ? `<br><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Ver aviso</a>` : ""}`;
}

function visibilidadPorZoom() {
  const lejos = ZONAS.mapa.getZoom() < 9;
  ZONAS.capaRegion.setStyle((f) => (f.properties.nombre === "Colombia"
    ? { fillOpacity: lejos ? 0.1 : 0, opacity: lejos ? 1 : 0 }
    : { fillOpacity: lejos ? 0.04 : 0, opacity: lejos ? 1 : 0 }));
  if (lejos) ZONAS.marcaBogota.addTo(ZONAS.mapa); else ZONAS.marcaBogota.remove();
}

function encuadrar(vista) {
  if (!ZONAS.mapa) return;
  let limites = ZONAS.limites[vista];
  if (vista === "localidad" && ZONAS.seleccion) {
    ZONAS.capaLoc.eachLayer((c) => { if (c.feature.properties.nombre === ZONAS.seleccion) limites = c.getBounds(); });
  }
  if (!limites) return;
  const opciones = { padding: [24, 24], maxZoom: vista === "localidad" ? 14 : 12 };
  // la primera vez el mapa aún no tiene vista: se encuadra sin animación
  if (ZONAS.mapa._loaded && !reducirMovimiento) ZONAS.mapa.flyToBounds(limites, { ...opciones, duration: 0.8 });
  else ZONAS.mapa.fitBounds(limites, opciones);
  document.querySelectorAll(".vistas button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.vista === vista)));
}

// ---------------------------------------------------------------- historial
const fechaDia = (iso) => new Date(iso.length <= 10 ? `${iso}T12:00:00` : iso).toLocaleDateString("es-CO", { day: "numeric", month: "short", year: "numeric" });

async function mostrarHistoria(boton) {
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

function lineaTendencia(semanas) {
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

async function pintarTendencia(nombre) {
  const caja = $("#z-tendencia");
  caja.textContent = "Calculando…";
  let t;
  try { t = await api(`/api/tendencia?localidad=${encodeURIComponent(nombre)}`); } catch (e) { caja.textContent = `No se pudo calcular: ${e.message}`; return; }
  if (ZONAS.seleccion !== nombre) return;
  const c = t.cambios_30_dias || {}, r = t.retirados || {};
  const semanasCon = t.semanas.filter((s) => s.suficiente).length;
  caja.innerHTML = `${t.suficiente
    ? `${lineaTendencia(t.semanas)}<p>m² mediano de los avisos que aparecieron cada semana: ${t.variacion_m2 >= 0 ? "subió" : "bajó"} ${fmt1.format(Math.abs(t.variacion_m2 * 100))} % desde la primera semana con datos.</p>`
    : `<p>Todavía no hay tendencia: se necesitan al menos ${t.minimo_semanas} semanas con ${t.minimo_por_semana} avisos nuevos o más (hay ${semanasCon}). Aparece sola a medida que el recolector trabaja.</p>`}
    <p>Cambios de precio en los últimos 30 días: ${c.n ? `${fmt.format(c.bajadas)} bajadas y ${fmt.format(c.subidas)} subidas (mediana ${pct(c.variacion_mediana)})` : "ninguno registrado"}.</p>
    <p>${r.n ? `Los ${fmt.format(r.n)} avisos ya retirados duraron publicados ${fmt.format(Math.round(r.dias_mediana))} días (mediana).` : "Ningún aviso de esta zona se ha retirado todavía, así que no se sabe cuánto duran publicados."}</p>`;
}

async function seleccionarZona(nombre, { encuadrar: mover = true } = {}) {
  if (!nombre) return;
  REG.debug(`Zona elegida: ${nombre}`);
  ZONAS.seleccion = nombre;
  $("#z-localidad").value = nombre;
  if (ZONAS.capaLoc) ZONAS.capaLoc.setStyle(estiloLocalidad);
  document.querySelector('.vistas [data-vista="localidad"]').disabled = false;
  document.querySelectorAll(".fila-rank").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.nombre === nombre)));
  if (mover) encuadrar("localidad");
  let d = ZONAS.cache.get(nombre);
  if (!d) {
    d = await api(`/api/zonas/${encodeURIComponent(nombre)}`);
    ZONAS.cache.set(nombre, d);
  }
  if (ZONAS.seleccion === nombre) { pintarPanelZona(d); if (d.zona.n) pintarTendencia(nombre); else $("#z-tendencia").innerHTML = ""; }
}

function pintarPanelZona(d) {
  const z = d.zona, total = ZONAS.datos.con_ranking;
  $("#z-vacio").hidden = true;
  $("#z-contenido").hidden = false;
  $("#z-nombre").textContent = z.nombre;
  if (!z.n) {
    $("#z-puesto").textContent = "Todavía no hay avisos de esta localidad en los datos. El ciclo automático los agregará si aparecen en el portal.";
    for (const id of ["#z-cifras", "#z-tipico", "#z-barrios", "#z-debajo"]) $(id).innerHTML = "";
    return;
  }
  $("#z-puesto").textContent = z.puesto
    ? `Puesto ${z.puesto} de ${total} por metro cuadrado (1 = la más cara).${z.suficiente ? "" : " Con menos de 10 avisos, las cifras son poco estables."}`
    : `Solo ${z.n} aviso${z.n > 1 ? "s" : ""}: muy pocos para compararla con las demás.`;
  const cifras = [
    ["Precio promedio", corto(z.promedio_precio), `mediana ${corto(z.mediana_precio)}`],
    ["Metro cuadrado mediano", m2(z.mediana_m2), z.vs_ciudad_pct != null ? `${pct(z.vs_ciudad_pct)} frente a Bogotá` : ""],
    ["Rango de precios", `${corto(z.p10_precio)} a ${corto(z.p90_precio)}`, "8 de cada 10 avisos"],
    ["Rango por metro cuadrado", `${m2(z.p10_m2)} a ${m2(z.p90_m2)}`, "8 de cada 10 avisos"],
    ["Avisos analizados", fmt.format(z.n), `área mediana ${area(z.mediana_area)}`],
    ["Frente a lo esperado", `${pctEntero(z.por_debajo_pct)} por debajo`, `${pctEntero(z.por_encima_pct)} por encima`],
  ];
  $("#z-cifras").innerHTML = cifras.map(([k, v, n]) => `<div><dt>${k}</dt><dd>${v}${n ? `<small>${esc(n)}</small>` : ""}</dd></div>`).join("");

  const t = d.tipico;
  if (t) {
    const desc = [`${fmt.format(t.datos.area_m2)} m²`, `estrato ${t.datos.estrato}`,
      t.datos.habitaciones != null ? `${t.datos.habitaciones} habitaciones` : null].filter(Boolean).join(", ");
    $("#z-tipico").innerHTML = `Un apartamento típico de ${esc(z.nombre)} (${desc}) se publicaría por
      <strong>${millones(t.precio)}</strong>Lo más probable: entre ${corto(t.minimo)} y ${corto(t.maximo)}.
      <br><button class="boton-texto" type="button" id="z-llevar">Calcular uno parecido</button>`;
    $("#z-llevar").addEventListener("click", () => llevarAlEstimador(t.datos));
  } else {
    $("#z-tipico").innerHTML = "";
  }

  $("#z-barrios").innerHTML = d.barrios.length
    ? d.barrios.map((b) => `<tr><td>${esc(b.barrio)}</td><td class="num">${fmt.format(b.n)}</td>
        <td class="num">${b.n >= 3 ? m2(b.mediana_m2) : "—"}</td>
        <td class="num">${b.n >= 3 ? pct(b.vs_localidad_pct) : "pocos avisos"}</td></tr>`).join("")
    : `<tr><td colspan="4" class="vacio">Los avisos de esta localidad no traen barrio.</td></tr>`;

  $("#z-debajo").innerHTML = d.debajo.length
    ? d.debajo.map((a) => {
      const url = urlSegura(a.url_origen);
      const desc = `${corto(a.precio)}, ${area(a.area_m2)}${a.barrio ? `, ${esc(a.barrio)}` : ""}`;
      return `<li><span>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${desc}</a>` : desc}${celdaFuente(a, true)}</span>${chipComparacion(a)}</li>`;
    }).join("")
    : `<li class="vacio">Ningún aviso está más de 10 % por debajo de lo esperado.</li>`;
}

function llevarAlEstimador(datos) {
  $("#area").value = datos.area_m2; $("#area-rango").value = datos.area_m2;
  $("#localidad").value = datos.localidad;
  cargarPerfil(datos.localidad);
  for (const n of ["habitaciones", "banos", "parqueaderos"]) {
    if (datos[n] != null) document.querySelector(`.contador[data-nombre=${n}]`).fijar(datos[n]);
  }
  $("#antiguedad").value = datos.antiguedad ?? "";
  elegirEstrato(datos.estrato, false);
  document.getElementById("estimar").scrollIntoView({ behavior: reducirMovimiento ? "auto" : "smooth" });
}

function pintarRanking() {
  const z = ZONAS.datos, caja = $("#z-ranking");
  const filas = z.localidades.filter((l) => l.n > 0).sort((a, b) => (b.n >= 3) - (a.n >= 3) || (b.mediana_m2 ?? 0) - (a.mediana_m2 ?? 0));
  if (!filas.length) { caja.innerHTML = `<p class="como-leer">Todavía no hay avisos con localidad.</p>`; return; }
  const validas = filas.filter((l) => l.n >= 3);
  const lo = Math.min(...validas.map((l) => l.p10_m2), z.ciudad.mediana_m2), hi = Math.max(...validas.map((l) => l.p90_m2), z.ciudad.mediana_m2);
  const x = (v) => `${((v - lo) / (hi - lo || 1)) * 100}%`;
  caja.innerHTML = filas.map((l) => (l.n >= 3
    ? `<button type="button" class="fila-rank" data-nombre="${esc(l.nombre)}" aria-pressed="${l.nombre === ZONAS.seleccion}">
        <span>${esc(l.nombre)} <small>(${fmt.format(l.n)})</small></span>
        <span class="pista-rank" aria-hidden="true"><span class="ref" style="left:${x(z.ciudad.mediana_m2)}"></span>
          <span class="bigote" style="left:${x(l.p10_m2)};width:calc(${x(l.p90_m2)} - ${x(l.p10_m2)})"></span>
          <span class="punto" style="left:${x(l.mediana_m2)}"></span></span>
        <span class="valor-rank">${m2(l.mediana_m2)}</span></button>`
    : `<div class="fila-rank fila-rank--pocos"><span>${esc(l.nombre)} <small>(${l.n})</small></span>
        <span class="como-leer" style="margin:0">muy pocos avisos</span><span></span></div>`)).join("");
  caja.querySelectorAll("button.fila-rank").forEach((b) => b.addEventListener("click", () => {
    seleccionarZona(b.dataset.nombre, { encuadrar: true });
    document.getElementById("zonas").scrollIntoView({ behavior: reducirMovimiento ? "auto" : "smooth" });
  }));
}

$("#z-localidad").addEventListener("change", (e) => seleccionarZona(e.target.value, { encuadrar: true }));
$("#z-ver-avisos").addEventListener("click", () => {
  $("#f-localidad").value = ZONAS.seleccion; paginaActual = 1; cargarAvisos();
  document.getElementById("avisos").scrollIntoView({ behavior: reducirMovimiento ? "auto" : "smooth" });
});

// ---------------------------------------------------------------- ubicación de la estimación en el mapa
// Cada estimación se ubica sola: en el mapa del resultado y en el de Zonas. Tocar una localidad del
// mapa del resultado la elige en el formulario y recalcula el precio.
const CENTROS = {   // punto aproximado de la zona construida de cada localidad, dentro de su límite
  "Usaquén": [4.715, -74.035], "Chapinero": [4.65, -74.06], "Santa Fe": [4.61, -74.07], "San Cristóbal": [4.565, -74.085],
  "Usme": [4.5, -74.11], "Tunjuelito": [4.575, -74.135], "Bosa": [4.615, -74.19], "Kennedy": [4.63, -74.15],
  "Fontibón": [4.675, -74.14], "Engativá": [4.705, -74.11], "Suba": [4.745, -74.08], "Barrios Unidos": [4.67, -74.075],
  "Teusaquillo": [4.64, -74.08], "Los Mártires": [4.605, -74.09], "Antonio Nariño": [4.59, -74.1],
  "Puente Aranda": [4.615, -74.115], "La Candelaria": [4.597, -74.072], "Rafael Uribe Uribe": [4.57, -74.115],
  "Ciudad Bolívar": [4.54, -74.15], "Sumapaz": [4.05, -74.3],
};
const MINI = { mapa: null, capaLoc: null, marca: null, vecinos: null, punto: null, nombrePunto: null, localidad: null };

function iconoEstimacion(precio) {
  return L.divIcon({ className: "pin-estimado", iconSize: [0, 0],
    html: `<span><small>Su apartamento</small>${precio == null ? "Sin comparables" : corto(precio)}</span>` });
}

function puntoDe(localidad, similares) {
  if (MINI.punto && MINI.nombrePunto === localidad) return MINI.punto;
  const cerca = similares.filter((s) => s.localidad === localidad && s.latitud && s.longitud);
  if (cerca.length) {
    return L.latLng(cerca.reduce((a, s) => a + s.latitud, 0) / cerca.length, cerca.reduce((a, s) => a + s.longitud, 0) / cerca.length);
  }
  return CENTROS[localidad] ? L.latLng(CENTROS[localidad]) : null;
}

function estiloMini(f) {
  const elegida = f.properties.nombre === MINI.localidad;
  return elegida
    ? { fillColor: "#B4472C", fillOpacity: 0.18, color: "#B4472C", weight: 2.5 }
    : { fillColor: "#17322C", fillOpacity: 0.03, color: "#7C9486", weight: 0.8 };
}

// Encuadre del mini mapa. Se espera un cuadro para que invalidateSize ya haya medido la tarjeta
// (recién mostrada) y se usa fitBounds: flyToBounds se cancela si llega otra estimación a mitad del vuelo.
function encuadrarMini(limites, opciones) {
  requestAnimationFrame(() => {
    MINI.mapa.invalidateSize({ animate: false });
    MINI.mapa.stop();
    MINI.mapa.fitBounds(limites, { ...opciones, animate: !reducirMovimiento });
  });
}

// Las calles de fondo vienen de internet (CARTO). Si fallan, se avisa una vez por mapa, no por cada tesela.
function tilesFallidas(mapa) {
  let n = 0;
  return (e) => {
    n++;
    if (n === 1) REG.warn(`No cargan las calles de fondo del ${mapa} (¿sin internet o bloqueadas por la red?). Los límites de las localidades sí se ven.`, e.tile?.src);
    else if (n % 50 === 0) REG.debug(`${n} teselas fallidas en el ${mapa}`);
  };
}

async function ubicarEstimacion(r, datos) {
  if (!window.L) { REG.warn("Mini mapa omitido: Leaflet no está cargado"); return; }
  const t = REG.tiempo("Ubicar la estimación en el mapa");
  try { await ubicarEstimacionSinRegistro(r, datos); t.fin({ localidad: datos.localidad, similares: r.similares.length }); } catch (e) { t.fallo(e); }
}

async function ubicarEstimacionSinRegistro(r, datos) {
  const geo = await datosGeo();
  if (!MINI.mapa) {
    MINI.mapa = L.map("mini-mapa", { scrollWheelZoom: false, zoomControl: false, attributionControl: true, minZoom: 9, maxZoom: 16, zoomSnap: 0.25 });
    L.control.zoom({ position: "topright" }).addTo(MINI.mapa);
    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>', maxZoom: 18,
    }).on("tileerror", tilesFallidas("mini mapa del resultado")).addTo(MINI.mapa);
    REG.debug("Mini mapa del resultado creado");
    MINI.capaLoc = L.geoJSON(geo.loc, {
      style: estiloMini,
      onEachFeature: (f, capa) => {
        const nombre = f.properties.nombre;
        capa.bindTooltip(nombre, { sticky: true, className: "etiqueta-loc" });
        capa.on("click", (e) => {
          if (!ESTADO.localidades.includes(nombre)) { REG.info(`Mini mapa: ${nombre} es rural y el modelo no la conoce; no se cambia la estimación`); return; }
          REG.info(`Mini mapa: tocó ${nombre}; se recalcula el precio`, { lat: e.latlng.lat, lng: e.latlng.lng });
          MINI.punto = e.latlng; MINI.nombrePunto = nombre;
          $("#localidad").value = nombre;
          cargarPerfil(nombre);
          programar();
        });
      },
    }).addTo(MINI.mapa);
    MINI.vecinos = L.layerGroup().addTo(MINI.mapa);
    MINI.mapa.fitBounds(L.latLngBounds([4.47, -74.215], [4.83, -74.01]));
  }
  MINI.mapa.invalidateSize();
  MINI.localidad = datos.localidad;
  MINI.capaLoc.setStyle(estiloMini);

  MINI.vecinos.clearLayers();
  for (const s of r.similares) {
    if (!s.latitud || !s.longitud) continue;
    L.circleMarker([s.latitud, s.longitud], { radius: 5, weight: 1.5, color: "#17322C", fillColor: "#FBFCFB", fillOpacity: 1 })
      .bindTooltip(`${corto(s.precio)}, ${area(s.area_m2)}${s.localidad ? `<br>${esc(s.localidad)}` : ""}`, { className: "etiqueta-loc" })
      .addTo(MINI.vecinos);
  }

  const pos = datos.localidad ? puntoDe(datos.localidad, r.similares) : null;
  if (MINI.marca) { MINI.marca.remove(); MINI.marca = null; }
  if (pos) {
    MINI.marca = L.marker(pos, { icon: iconoEstimacion(r.precio), keyboard: false, zIndexOffset: 1000 }).addTo(MINI.mapa);
    let limites = null;
    MINI.capaLoc.eachLayer((c) => { if (c.feature.properties.nombre === datos.localidad) limites = c.getBounds(); });
    if (limites) encuadrarMini(limites, { padding: [18, 18], maxZoom: 13.5 });
    $("#mini-ayuda").textContent = `Ubicado en ${datos.localidad}. Toque otra localidad para moverlo; el precio se recalcula solo.`;
  } else {
    encuadrarMini(L.latLngBounds([4.47, -74.215], [4.83, -74.01]), {});
    $("#mini-ayuda").textContent = "Toque una localidad del mapa para ubicar el apartamento; también puede elegirla en el formulario.";
  }
  MINI.ultimo = { pos, r, datos };
  marcarEnZonas(pos, r, datos);
}

// El mapa grande de Zonas acompaña a la estimación: misma localidad y misma marca.
function marcarEnZonas(pos, r, datos) {
  if (!ZONAS.mapa) return;
  if (ZONAS.marcaEstimacion) { ZONAS.marcaEstimacion.remove(); ZONAS.marcaEstimacion = null; }
  if (!pos) return;
  ZONAS.marcaEstimacion = L.marker(pos, { icon: iconoEstimacion(r.precio), keyboard: false, zIndexOffset: 1000 }).addTo(ZONAS.mapa);
  if (datos.localidad !== ZONAS.seleccion) seleccionarZona(datos.localidad, { encuadrar: true });
}

// ---------------------------------------------------------------- actualización automática
let FIRMA_DATASET = null, sondeo = null;
const RESULTADOS = { completo: "Completo", bloqueado: "Bloqueado por el portal", sin_portales: "Sin portales autorizados",
  error: "Error", sin_conexion: "Sin conexión", en_curso: "Otro ciclo en curso" };
const hora = (iso) => new Date(iso).toLocaleTimeString("es-CO", { hour: "numeric", minute: "2-digit" });
const fechaCorta = (iso) => new Date(iso).toLocaleString("es-CO", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });

let situacionAnterior = null;
async function cargarActualizacion() {
  clearTimeout(sondeo);
  let a;
  try { a = await api("/api/automatizacion"); } catch (e) {
    REG.warn("No se pudo consultar el estado de la actualización; se reintenta en 1 minuto", e.message);
    sondeo = setTimeout(cargarActualizacion, 60000); return;
  }
  if (a.situacion !== situacionAnterior) {
    REG.info(`Actualización automática: ${a.situacion}${a.fase ? ` (${a.fase})` : ""}`, { ultimo: a.ultimo_ciclo, proximo: a.proximo_ciclo });
    situacionAnterior = a.situacion;
  }
  try { pintarActualizacion(a); } catch (e) { REG.error("Falló al mostrar el estado de la actualización", e); }
  try { pintarAvisoFuente(a); } catch (e) { REG.error("Falló al mostrar el estado de la fuente", e); }
  if (ESTADO?.dataset?.tipo === "ninguno" && a.dataset.tipo !== "ninguno") {
    REG.info("Ya hay datos reales: se recarga la página para mostrarlos");
    location.reload();
    return;
  }
  const firma = `${a.dataset.archivo}|${a.dataset.avisos}`;
  if (FIRMA_DATASET && firma !== FIRMA_DATASET) refrescarTodo();
  FIRMA_DATASET = firma;
  sondeo = setTimeout(cargarActualizacion, a.situacion === "en_curso" ? 5000 : 60000);
}

function pintarActualizacion(a) {
  const tarjeta = $("#ac-estado"), u = a.ultimo_ciclo;
  tarjeta.dataset.situacion = a.situacion;
  const textos = {
    en_curso: ["Actualizando ahora", `${a.fase || "Preparando"}${a.detalle ? `: ${a.detalle}` : ""}.`],
    esperando: ["Datos al día", `${u ? `Última actualización: ${fechaCorta(u.fin || u.inicio)}. ` : ""}${a.proximo_ciclo ? `Próxima: ${fechaCorta(a.proximo_ciclo)}.` : ""}`],
    pausado: ["En pausa", `El portal limitó el acceso. Se vuelve a intentar a las ${a.pausa_hasta ? hora(a.pausa_hasta) : "—"}; insistir antes podría bloquear la recolección.`],
    sin_portales: ["Recolección sin autorizar", "Ningún portal tiene los términos de uso revisados. Una persona del equipo debe leerlos y poner terminos_revisados: true en config/portales.yaml; desde ese momento la recolección empieza sola."],
    apagado: ["Actualización automática apagada", "Se enciende con automatico.activo: true en config/portales.yaml (y sin la variable VIVIENDA_AUTOMATICO=0)."],
  };
  const [titulo, detalle] = textos[a.situacion] ?? ["Consultando…", ""];
  $("#ac-situacion").textContent = titulo;
  $("#ac-detalle").textContent = detalle;
  const m = a.situacion === "en_curso" && /(\d+) de (\d+)/.exec(a.detalle || "");
  $("#ac-progreso").hidden = !m;
  if (m) $("#ac-progreso span").style.width = `${(Number(m[1]) / Number(m[2])) * 100}%`;
  $("#ac-ejecutar").hidden = !(a.puede_ejecutar && a.situacion === "esperando");
  $("#ac-registros").hidden = !a.puede_ejecutar;
  $("#ac-nota").textContent = u && u.resultado !== "completo" && u.mensaje ? `Último ciclo: ${u.mensaje}` : "";

  const b = a.base || {};
  const sp = a.segundo_plano || {};
  const ritmo = (b.nuevos_24_h || 0) / 24;
  const falta = ritmo > 0 && b.pendientes ? Math.ceil(b.pendientes / ritmo) : null;
  $("#ac-segundo-plano").hidden = !sp.activo;
  if (sp.activo) {
    $("#ac-segundo-plano").textContent = `El recolector trabaja en segundo plano desde ${fechaCorta(sp.desde)} y sigue aunque cierre esta página o la aplicación (mientras el computador esté encendido). En las últimas 24 h guardó ${fmt.format(b.nuevos_24_h || 0)} avisos nuevos y verificó ${fmt.format(b.verificados_24_h || 0)}.${falta ? ` A este ritmo, los ${fmt.format(b.pendientes)} pendientes se leen en unas ${fmt.format(falta)} h.` : ""}`;
  }
  const base = [["Enlaces conocidos", b.enlaces], ["Pendientes por leer", b.pendientes], ["Avisos guardados", b.guardados],
    ["Disponibles hoy", b.disponibles], ["Nuevos, últimas 24 h", b.nuevos_24_h], ["Verificados, últimas 24 h", b.verificados_24_h],
    ["Retirados del portal", b.retirados], ["Cambios de precio, 7 días", b.cambios_precio_7_dias]];
  $("#ac-base").innerHTML = base.map(([k, v]) => `<div><dt>${k}</dt><dd>${fmt.format(v ?? 0)}</dd></div>`).join("");

  $("#ac-ejecuciones").innerHTML = a.ejecuciones.length
    ? a.ejecuciones.map((e) => `<tr><td>${fechaCorta(e.inicio)}</td>
        <td title="${esc(e.mensaje || "")}">${esc(RESULTADOS[e.resultado] ?? e.resultado ?? "—")}</td>
        <td class="num">${fmt.format(e.enlaces_nuevos ?? 0)}</td><td class="num">${fmt.format(e.avisos_nuevos ?? 0)}</td>
        <td class="num">${fmt.format(e.revisados ?? 0)}</td><td class="num">${fmt.format(e.con_cambios ?? 0)}</td>
        <td class="num">${fmt.format(e.retirados ?? 0)}</td><td class="num">${e.version_dataset ? `v${e.version_dataset}` : "—"}</td></tr>`).join("")
    : `<tr><td colspan="8" class="vacio">Todavía no se ha ejecutado ningún ciclo.</td></tr>`;

  const insignia = $("#insignia-datos");
  insignia.classList.toggle("actualizando", a.situacion === "en_curso");
  $("#insignia-texto").textContent = a.situacion === "en_curso" ? "Verificando con la fuente…" : textoDataset(a.dataset, a.verificacion);
  if (!$("#sin-datos").hidden) {
    $("#sd-base").innerHTML = base.slice(0, 3).map(([k, v]) => `<div><dt>${k}</dt><dd>${fmt.format(v ?? 0)}</dd></div>`).join("");
    $("#sd-estado").textContent = `${titulo}. ${detalle}`;
  }
}

$("#ac-ejecutar").addEventListener("click", async () => {
  const boton = $("#ac-ejecutar");
  boton.disabled = true;
  try {
    await api("/api/automatizacion/ejecutar", { method: "POST" });
    REG.info("Ciclo iniciado a mano desde la página");
    $("#ac-nota").textContent = "Ciclo iniciado.";
  } catch (e) {
    // 409 = ya hay un ciclo corriendo (la página aún no se había enterado): no es una falla.
    $("#ac-nota").textContent = /ciclo en curso/i.test(e.message)
      ? "Ya se está actualizando: el avance aparece arriba." : `No se pudo iniciar: ${e.message}`;
  } finally {
    boton.disabled = false;
    setTimeout(cargarActualizacion, 300);
  }
});

// Franja de aviso arriba: si no se pudo verificar con la fuente, se dice claramente y desde cuándo
// son los datos que se están viendo. Nada desactualizado se presenta como vigente.
function pintarAvisoFuente(a) {
  const u = a.ultimo_ciclo, v = a.verificacion || {};
  const desde = v.ultima_verificacion ? `Los datos que ve se verificaron con la fuente por última vez ${haceCuanto(v.ultima_verificacion)} (${fechaLarga(v.ultima_verificacion)}).` : "";
  const viejos = v.sin_verificar_reciente ? ` ${fmt.format(v.sin_verificar_reciente)} avisos llevan más de ${v.dias_vigencia} días sin verificar y se marcan así en la lista.` : "";
  let texto = "";
  if (a.situacion === "pausado") {
    texto = `El portal limitó el acceso y la verificación está en pausa hasta las ${a.pausa_hasta ? hora(a.pausa_hasta) : "—"}. ${desde}${viejos}`;
  } else if (a.situacion !== "en_curso" && u && ["sin_conexion", "error", "bloqueado"].includes(u.resultado)) {
    texto = `No se pudo verificar con la fuente en el último intento (${u.mensaje || RESULTADOS[u.resultado]}). ${desde}${viejos}`;
  } else if (a.situacion === "sin_portales") {
    texto = `La recolección no está autorizada (falta terminos_revisados: true en config/portales.yaml), así que los datos no se están verificando. ${desde}`;
  } else if (a.situacion === "apagado") {
    texto = `La actualización automática está apagada: los datos no se están verificando con la fuente. ${desde}`;
  }
  const caja = $("#aviso-fuente");
  if (Boolean(texto) !== !caja.hidden) REG.info(texto ? `Aviso de fuente: ${texto}` : "La fuente volvió a estar verificada");
  caja.hidden = !texto;
  $("#aviso-fuente-texto").textContent = texto;
}

// Marcas de la limpieza: atípico (no entra a cálculos) y proyecto nuevo.
function marcas(a) {
  return `${a.atipico ? ` <span class="etiqueta-limpieza etiqueta-limpieza--atipico" title="${esc(a.atipico)}. Se muestra, pero no entra al modelo, a los comparables ni a las estadísticas.">atípico</span>` : ""}${
    a.proyecto ? ` <span class="etiqueta-limpieza" title="Proyecto nuevo o para estrenar">nuevo</span>` : ""}`;
}

// ---------------------------------------------------------------- avisos
let paginaActual = 1;
async function cargarAvisos() {
  const f = new FormData($("#filtros"));
  const q = new URLSearchParams({ pagina: paginaActual, por_pagina: 20 });
  for (const [k, v] of f) if (v) q.set(k, v);
  const r = await api(`/api/avisos?${q}`);
  const paginas = Math.max(1, Math.ceil(r.total / r.por_pagina));
  $("#a-conteo").textContent = r.total === 0
    ? "Ningún aviso cumple estos filtros. Pruebe quitando alguno."
    : `${fmt.format(r.total)} avisos cumplen los filtros.`;
  $("#a-filas").innerHTML = r.avisos.map((a) => `<tr>
      <td>${corto(a.precio)}${precioAnterior(a)}</td><td>${chipComparacion(a)}</td><td>${m2(a.precio_m2)}</td><td>${area(a.area_m2)}</td>
      <td>${a.habitaciones ?? "—"}</td><td>${a.banos ?? "—"}</td><td>${chipEstrato(a.estrato)}</td>
      <td>${esc(a.localidad ?? "—")}${marcas(a)}</td><td>${esc(a.barrio ?? "—")}</td><td>${celdaFuente(a)}${a.url_origen ? `<button class="boton-texto boton-historia" type="button" data-url="${esc(a.url_origen)}" aria-expanded="false">Historial</button>` : ""}</td><td>${enlaceAviso(a.url_origen)}</td></tr>`).join("");
  $("#a-pagina").textContent = `Página ${r.pagina} de ${paginas}`;
  $("#a-limpiar").hidden = !["localidad", "estrato", "precio_max", "area_min", "comparacion"].some((k) => f.get(k));
  $("#a-anterior").disabled = r.pagina <= 1;
  $("#a-siguiente").disabled = r.pagina >= paginas;
}
const recargarAvisos = () => seguro("Cargar avisos", cargarAvisos, avisoEnSeccion("#a-filas", "la lista de avisos"));
$("#filtros").addEventListener("change", () => { paginaActual = 1; REG.debug("Filtros de avisos", Object.fromEntries(new FormData($("#filtros")))); recargarAvisos(); });
$("#a-filas").addEventListener("click", (e) => {
  const b = e.target.closest(".boton-historia");
  if (b) mostrarHistoria(b);
});
$("#a-anterior").addEventListener("click", () => { paginaActual--; recargarAvisos(); });
$("#a-siguiente").addEventListener("click", () => { paginaActual++; recargarAvisos(); });
$("#a-limpiar").addEventListener("click", () => {
  const f = $("#filtros"); const orden = f.orden.value;
  f.reset(); f.orden.value = orden; paginaActual = 1; recargarAvisos();
});

// ---------------------------------------------------------------- modelo
async function cargarModelo() {
  const m = await api("/api/modelo");
  const d = m.dataset, elegido = m.modelo_elegido, p = m.prueba_final[elegido], base = m.prueba_final["Línea base"];
  const veredicto = m.supera_linea_base
    ? `El ${esc(elegido.toLowerCase())} le gana a la línea base, así que es el que responde las estimaciones.`
    : `Ninguno de los tres modelos de árboles superó a la línea base, así que la aplicación usa la línea base. Es la decisión que el proyecto fijó de antemano: un modelo más complejo solo se usa si mejora el resultado.`;
  const advertencia = d.tipo === "demo"
    ? `<p><strong>Estas métricas salen de datos sintéticos</strong> y no dicen nada sobre el mercado real.</p>`
    : d.avisos < 300 ? `<p><strong>Con ${fmt.format(d.avisos)} avisos estas métricas son inestables.</strong> El proyecto se propuso reunir entre 500 y 1.500 antes de sacar conclusiones.</p>` : "";
  $("#mo-texto").innerHTML = `
    <p class="veredicto">${veredicto}</p>
    <p>La línea base es la cuenta que haría cualquiera con una hoja de cálculo: el precio mediano del metro cuadrado en la misma localidad y estrato, multiplicado por el área. Frente a ella se comparan un árbol de decisión, un bosque aleatorio y un modelo de <i>gradient boosting</i>. Los árboles aprenden el logaritmo del precio para que los apartamentos más caros no dominen.</p>
    <p>El rango de precio se calcula con los errores que el modelo comete en avisos que no vio (predicción conformal): debería contener el precio real ${Math.round(m.rango.nivel * 10)} de cada 10 veces.</p>
    ${advertencia}
    <p>Entrenado el ${new Date(m.fecha).toLocaleString("es-CO", { dateStyle: "long", timeStyle: "short" })} con <code>${esc(d.archivo)}</code>: ${fmt.format(d.entrenamiento)} avisos para entrenar y ${fmt.format(d.prueba)} reservados para la prueba final. Para volver a entrenar: <code>python -m modelo.entrenar</code>.</p>`;
  $("#mo-cv").innerHTML = m.validacion_cruzada.resultados.map((r) => `<tr class="${r.modelo === elegido ? "elegido" : ""}">
      <td>${esc(r.modelo)}</td><td class="num">${corto(r.mae)}</td><td class="num">${fmt1.format(r.error_relativo * 100)} %</td>
      <td class="num">${r.r2 == null ? "—" : r.r2.toLocaleString("es-CO", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td></tr>`).join("");
  const filas = [
    ["Error promedio (MAE)", corto(p.mae)],
    ["Error relativo", `${fmt1.format(p.error_relativo * 100)} %`],
    ["Meta del proyecto", "15 %", "meta"],
    [`Cobertura del rango (meta ${Math.round(m.rango.nivel * 100)} %)`, `${Math.round((p.cobertura_rango ?? 0) * 100)} %`],
  ];
  if (elegido !== "Línea base" && base) filas.splice(2, 0, ["Línea base en la misma prueba", corto(base.mae)]);
  $("#mo-prueba").innerHTML = filas.map(([k, v, c]) => `<div${c ? ` class="${c}"` : ""}><dt>${k}</dt><dd>${v}</dd></div>`).join("");
  pintarPrecision(m);
}

// ¿Cuánto aciertan de verdad? Prueba con avisos reales estimados como si fueran nuevos.
function pintarPrecision(m) {
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

// ---------------------------------------------------------------- navegación y precio flotante
// El menú marca la sección que se está leyendo.
const enlacesNav = [...document.querySelectorAll(".barra__nav a")];
const espia = new IntersectionObserver((entradas) => {
  for (const e of entradas) {
    if (!e.isIntersecting) continue;
    for (const a of enlacesNav) a.setAttribute("aria-current", String(a.hash === `#${e.target.id}`));
  }
}, { rootMargin: "-40% 0px -55% 0px" });
for (const id of ["estimar", "zonas", "mercado", "avisos", "actualizacion", "modelo"]) espia.observe(document.getElementById(id));

// En el celular, mientras se ajusta el formulario, el precio queda visible abajo.
let hayResultado = false, formularioVisible = false, resumenVisible = false;
function actualizarFlotante() {
  $("#precio-flotante").hidden = !(hayResultado && formularioVisible && !resumenVisible);
}
new IntersectionObserver(([e]) => { formularioVisible = e.isIntersecting; actualizarFlotante(); }).observe($("#formulario"));
new IntersectionObserver(([e]) => { resumenVisible = e.isIntersecting; actualizarFlotante(); }, { threshold: 0.35 }).observe($("#resultado"));

// ---------------------------------------------------------------- arranque
// La cinta vacía del estado inicial
(function cintaFantasma() {
  const svg = document.querySelector(".cinta--fantasma");
  el("rect", { class: "franja", x: 220, y: 18, width: 170, height: 30, rx: 3 }, svg);
  el("line", { class: "regla", x1: 10, x2: 590, y1: 48, y2: 48 }, svg);
  for (let i = 0; i <= 29; i++) el("line", { class: "marca-m", x1: 10 + i * 20, x2: 10 + i * 20, y1: 48, y2: i % 5 ? 53 : 60, "stroke-width": 1 }, svg);
})();

iniciar().catch((e) => {
  REG.error("La página no pudo arrancar", e);
  $("#insignia-datos").textContent = "No se pudo conectar con la API";
  $("#resultado-vacio p").textContent = `La aplicación no respondió (${e.message}). Revise la terminal donde la inició.`;
});
