// Vivienda Bogotá: interfaz sin dependencias (salvo Leaflet para el mapa).
const $ = (s, r = document) => r.querySelector(s);
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

async function api(ruta, opciones) {
  const r = await fetch(ruta, opciones);
  if (!r.ok) {
    let detalle = `Error ${r.status}`;
    try { const j = await r.json(); detalle = typeof j.detail === "string" ? j.detail : j.detail?.[0]?.msg || detalle; } catch {}
    throw new Error(detalle);
  }
  return r.json();
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

const textoDataset = (d) => (d.tipo === "demo"
  ? `${fmt.format(d.avisos)} avisos de demostración`
  : `${fmt.format(d.avisos)} avisos reales, archivo ${d.archivo}`);

function pintarDataset() {
  const d = ESTADO.dataset;
  $("#insignia-texto").textContent = textoDataset(d);
  $("#aviso-demo").hidden = d.tipo !== "demo";
}

async function iniciar() {
  ESTADO = await api("/api/estado");
  pintarDataset();
  for (const sel of [$("#localidad"), $("#f-localidad")]) {
    for (const l of ESTADO.localidades) sel.add(new Option(l, l));
  }
  construirFormulario(ESTADO.referencia);
  estimarAhora();
  cargarMercado();
  cargarZonas();
  cargarAvisos();
  cargarModelo();
  cargarActualizacion();
}

// Cuando el ciclo automático publica un dataset nuevo, toda la página se pone al día sin recargar.
async function refrescarTodo() {
  ESTADO = await api("/api/estado");
  pintarDataset();
  ZONAS.cache.clear();
  await Promise.all([cargarMercado(), cargarZonas(), cargarAvisos(), cargarModelo()]);
  estimarAhora();
}

// ---------------------------------------------------------------- formulario
let estratoElegido = 4;

// La estimación vive en la URL (?area=70&estrato=4…): se puede guardar, compartir y volver a ella.
const CAMPOS_URL = ["area", "estrato", "localidad", "habitaciones", "banos", "parqueaderos", "antiguedad"];
let interactuo = false;   // false = el precio en pantalla es el ejemplo inicial, no el apartamento del usuario

function leerURL() {
  const q = new URLSearchParams(location.search);
  const entero = (k, lo, hi) => { const v = parseInt(q.get(k), 10); return Number.isFinite(v) && v >= lo && v <= hi ? v : null; };
  const d = {
    area: entero("area", 15, 1000), estrato: entero("estrato", 1, 6), habitaciones: entero("habitaciones", 0, 8),
    banos: entero("banos", 0, 8), parqueaderos: entero("parqueaderos", 0, 6), antiguedad: entero("antiguedad", 0, 100),
    localidad: ESTADO.localidades.includes(q.get("localidad")) ? q.get("localidad") : null,
  };
  if (d.area != null) { $("#area").value = d.area; $("#area-rango").value = d.area; }
  if (d.localidad) $("#localidad").value = d.localidad;
  if (d.antiguedad != null) $("#antiguedad").value = d.antiguedad;
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
    b.type = "button"; b.textContent = e; b.setAttribute("role", "radio");
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
    c.appendChild(control);
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
  $("#localidad").addEventListener("change", programar);
  $("#antiguedad").addEventListener("input", programar);
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
  };
}

let ultimaPeticion = 0;
async function estimarAhora() {
  const datos = leerFormulario();
  const error = $("#error-form");
  if (!datos.area_m2 || datos.area_m2 < 15 || datos.area_m2 > 1000) {
    error.textContent = "Escriba un área entre 15 y 1.000 m²."; error.hidden = false; return;
  }
  error.hidden = true;
  const form = $("#formulario"); form.classList.add("calculando");
  const id = ++ultimaPeticion;
  try {
    const r = await api("/api/estimar", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(datos) });
    if (id === ultimaPeticion) pintarResultado(r, datos);
  } catch (e) {
    error.textContent = `No se pudo estimar: ${e.message}`; error.hidden = false;
  } finally { if (id === ultimaPeticion) form.classList.remove("calculando"); }
}

// ---------------------------------------------------------------- resultado
let primeraVez = true;

function pintarResultado(r, datos) {
  $("#resultado-vacio").hidden = true;
  $("#resultado-lleno").hidden = false;
  $("#detalle").hidden = false;
  $("#resultado").setAttribute("aria-busy", "false");
  const rotulo = $("#r-rotulo");
  rotulo.textContent = interactuo ? "Precio de publicación estimado" : "Ejemplo con un apartamento típico: cambie los datos para ver el suyo";
  rotulo.classList.toggle("resultado__rotulo--ejemplo", !interactuo);
  if (interactuo) escribirURL(datos);
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
    caja.innerHTML = `<p class="efectos__vacio">Este apartamento se parece al típico, o el modelo (${esc(r.modelo)}) no usa las características en las que difiere.</p>`;
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
    <td>${esc(s.localidad ?? "—")}</td><td>${enlaceAviso(s.url_origen)}</td></tr>`).join("");
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
  const p99 = (arr) => { const s = [...arr].sort((a, b) => a - b); return s[Math.floor(s.length * 0.99)] ?? s.at(-1); };
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

async function datosGeo() {
  if (!ZONAS.geo) {
    const [loc, reg] = await Promise.all([fetch("/static/geo/localidades.geojson").then((r) => r.json()),
      fetch("/static/geo/region.geojson").then((r) => r.json())]);
    ZONAS.geo = { loc, reg };
  }
  return ZONAS.geo;
}

async function cargarZonas() {
  const [z, geo] = await Promise.all([api("/api/zonas"), datosGeo()]);
  const puntos = MERCADO ? MERCADO.mapa : (await api("/api/mercado")).mapa;
  ZONAS.datos = z;
  ZONAS.porNombre = new Map(z.localidades.map((l) => [l.nombre, l]));
  const conDatos = z.localidades.filter((l) => l.n >= 3);
  const m2s = conDatos.map((l) => l.mediana_m2);
  ZONAS.escala = m2s.length ? [Math.min(...m2s), Math.max(...m2s)] : [0, 1];
  $("#z-escala-min").textContent = m2s.length ? m2(ZONAS.escala[0]) : "";
  $("#z-escala-max").textContent = m2s.length ? m2(ZONAS.escala[1]) : "";

  const estables = conDatos.filter((l) => l.n >= 10).sort((a, b) => b.mediana_m2 - a.mediana_m2);
  $("#z-entrada").textContent = estables.length >= 2
    ? `${estables[0].nombre} es la localidad con el metro cuadrado más caro (${m2(estables[0].mediana_m2)}) y ${estables.at(-1).nombre} la más barata (${m2(estables.at(-1).mediana_m2)}). Elija una localidad en el mapa o en la lista para ver su análisis.`
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
    }).addTo(mapa);
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

async function seleccionarZona(nombre, { encuadrar: mover = true } = {}) {
  if (!nombre) return;
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
  if (ZONAS.seleccion === nombre) pintarPanelZona(d);
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
      return `<li><span>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${desc}</a>` : desc}</span>${chipComparacion(a)}</li>`;
    }).join("")
    : `<li class="vacio">Ningún aviso está más de 10 % por debajo de lo esperado.</li>`;
}

function llevarAlEstimador(datos) {
  $("#area").value = datos.area_m2; $("#area-rango").value = datos.area_m2;
  $("#localidad").value = datos.localidad;
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

// ---------------------------------------------------------------- actualización automática
let FIRMA_DATASET = null, sondeo = null;
const RESULTADOS = { completo: "Completo", bloqueado: "Bloqueado por el portal", sin_portales: "Sin portales autorizados",
  error: "Error", sin_conexion: "Sin conexión", en_curso: "Otro ciclo en curso" };
const hora = (iso) => new Date(iso).toLocaleTimeString("es-CO", { hour: "numeric", minute: "2-digit" });
const fechaCorta = (iso) => new Date(iso).toLocaleString("es-CO", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });

async function cargarActualizacion() {
  clearTimeout(sondeo);
  let a;
  try { a = await api("/api/automatizacion"); } catch { sondeo = setTimeout(cargarActualizacion, 60000); return; }
  pintarActualizacion(a);
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
  $("#ac-nota").textContent = u && u.resultado !== "completo" && u.mensaje ? `Último ciclo: ${u.mensaje}` : "";

  const b = a.base || {};
  const base = [["Enlaces conocidos", b.enlaces], ["Pendientes por leer", b.pendientes], ["Avisos guardados", b.guardados],
    ["Disponibles hoy", b.disponibles], ["Retirados del portal", b.retirados], ["Cambios de precio, 7 días", b.cambios_precio_7_dias]];
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
  $("#insignia-texto").textContent = a.situacion === "en_curso" ? "Actualizando datos…" : textoDataset(a.dataset);
}

$("#ac-ejecutar").addEventListener("click", async () => {
  const boton = $("#ac-ejecutar");
  boton.disabled = true;
  try {
    await api("/api/automatizacion/ejecutar", { method: "POST" });
    $("#ac-nota").textContent = "Ciclo iniciado.";
  } catch (e) {
    $("#ac-nota").textContent = `No se pudo iniciar: ${e.message}`;
  } finally {
    boton.disabled = false;
    setTimeout(cargarActualizacion, 1500);
  }
});

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
      <td>${esc(a.localidad ?? "—")}</td><td>${esc(a.barrio ?? "—")}</td><td>${enlaceAviso(a.url_origen)}</td></tr>`).join("");
  $("#a-pagina").textContent = `Página ${r.pagina} de ${paginas}`;
  $("#a-limpiar").hidden = !["localidad", "estrato", "precio_max", "area_min", "comparacion"].some((k) => f.get(k));
  $("#a-anterior").disabled = r.pagina <= 1;
  $("#a-siguiente").disabled = r.pagina >= paginas;
}
$("#filtros").addEventListener("change", () => { paginaActual = 1; cargarAvisos(); });
$("#a-anterior").addEventListener("click", () => { paginaActual--; cargarAvisos(); });
$("#a-siguiente").addEventListener("click", () => { paginaActual++; cargarAvisos(); });
$("#a-limpiar").addEventListener("click", () => {
  const f = $("#filtros"); const orden = f.orden.value;
  f.reset(); f.orden.value = orden; paginaActual = 1; cargarAvisos();
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
  $("#insignia-datos").textContent = "No se pudo conectar con la API";
  $("#resultado-vacio p").textContent = `La aplicación no respondió (${e.message}). Revise la terminal donde la inició.`;
});
