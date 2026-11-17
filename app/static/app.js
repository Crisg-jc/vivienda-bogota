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

async function iniciar() {
  ESTADO = await api("/api/estado");
  const d = ESTADO.dataset;
  $("#insignia-datos").textContent = d.tipo === "demo"
    ? `${fmt.format(d.avisos)} avisos de demostración`
    : `${fmt.format(d.avisos)} avisos reales, archivo ${d.archivo}`;
  $("#aviso-demo").hidden = d.tipo !== "demo";

  for (const sel of [$("#localidad"), $("#f-localidad")]) {
    for (const l of ESTADO.localidades) sel.add(new Option(l, l));
  }
  construirFormulario(ESTADO.referencia);
  estimarAhora();
  cargarMercado();
  cargarAvisos();
  cargarModelo();
}

// ---------------------------------------------------------------- formulario
let estratoElegido = 4;

function construirFormulario(ref) {
  const grupo = $("#estratos");
  estratoElegido = Math.round(parseFloat(ref.estrato)) || 4;
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
function programar() { clearTimeout(temporizador); temporizador = setTimeout(estimarAhora, 350); }

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
  $("#r-precio").textContent = millones(r.precio);
  $("#pf-precio").textContent = millones(r.precio);
  hayResultado = true; actualizarFlotante();
  $("#r-rango").textContent = `Lo más probable es que un aviso así pida entre ${corto(r.minimo)} y ${corto(r.maximo)}.`;
  $("#r-m2").textContent = m2(r.precio_m2);
  const c = r.contexto;
  $("#r-contexto").textContent = c.estrato_avisos
    ? `Es más alto que el del ${c.percentil_m2} % de los ${fmt.format(c.estrato_avisos)} avisos de estrato ${datos.estrato}, cuya mediana es ${m2(c.estrato_mediana_m2)}.`
    : `No hay avisos de estrato ${datos.estrato} para comparar.`;

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
    conTip(c, `${corto(s.precio)}, ${area(s.area_m2)}<br>Estrato ${s.estrato ?? "sin dato"}${s.localidad ? `, ${s.localidad}` : ""}`);
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
    caja.innerHTML = `<p class="efectos__vacio">Este apartamento se parece al típico, o el modelo (${r.modelo}) no usa las características en las que difiere.</p>`;
    return;
  }
  const maximo = Math.max(...r.efectos.map((e) => Math.abs(e.efecto)));
  for (const e of r.efectos) {
    const sube = e.efecto > 0;
    const fila = document.createElement("div"); fila.className = "efecto";
    const ancho = (Math.abs(e.efecto) / maximo) * 50;
    fila.innerHTML = `<span class="efecto__nombre">${e.nombre}: ${e.valor}<small>típico: ${e.tipico}</small></span>
      <span class="efecto__pista" aria-hidden="true"><span class="efecto__barra efecto__barra--${sube ? "sube" : "baja"}" style="width:${ancho}%"></span></span>
      <span class="efecto__valor">${sube ? "+" : "−"}${corto(Math.abs(e.efecto))}</span>`;
    caja.appendChild(fila);
  }
}

function enlaceAviso(url) {
  return url ? `<a href="${url}" target="_blank" rel="noopener">Ver aviso<span class="sr"> (se abre en otra pestaña)</span></a>` : "";
}
function chipEstrato(e) {
  if (e == null) return "—";
  const oscuro = e >= 4;
  return `<span class="chip-estrato" style="background:${COLOR_ESTRATO[e]};color:${oscuro ? "#fff" : "var(--paramo)"}">${e}</span>`;
}

function pintarSimilares(lista) {
  $("#r-similares").innerHTML = lista.map((s) => `<tr>
    <td>${corto(s.precio)}</td><td>${m2(s.precio_m2)}</td><td>${area(s.area_m2)}</td><td>${chipEstrato(s.estrato)}</td>
    <td>${s.localidad ?? "—"}</td><td>${enlaceAviso(s.url_origen)}</td></tr>`).join("");
}

// ---------------------------------------------------------------- mercado
async function cargarMercado() {
  const m = await api("/api/mercado");
  const r = m.resumen;
  $("#m-resumen").innerHTML = `La mitad de los ${fmt.format(r.avisos)} avisos pide menos de <strong>${millones(r.mediana_precio)}</strong>.
    El metro cuadrado mediano está en <strong>${m2(r.mediana_m2)}</strong> y el área mediana es de <strong>${area(r.mediana_area)}</strong>.`;
  pintarCajas(m.estratos);
  if (m.estratos_omitidos.length) {
    const lista = m.estratos_omitidos.map((o) => `${o.estrato} (${o.n})`).join(", ");
    $("#g-cajas").insertAdjacentHTML("afterend", `<p class="como-leer">No se dibuja caja para los estratos con menos de 3 avisos: ${lista}. Entre paréntesis, cuántos avisos hay.</p>`);
  }
  pintarDispersion(m.dispersion);
  let ultimoAncho = innerWidth, espera;
  addEventListener("resize", () => {
    if (innerWidth === ultimoAncho) return;
    ultimoAncho = innerWidth; clearTimeout(espera);
    espera = setTimeout(() => { pintarCajas(m.estratos); pintarDispersion(m.dispersion); }, 200);
  });
  pintarLocalidades(m.localidades);
  pintarMapa(m.mapa);
}

function pintarCajas(estratos) {
  const svg = $("#g-cajas"); svg.replaceChildren();
  if (!estratos.length) { svg.outerHTML = `<p class="como-leer">No hay suficientes avisos por estrato para dibujar cajas.</p>`; return; }
  // se dibuja al ancho real del contenedor para que el texto conserve su tamaño en cualquier pantalla
  const W = Math.max(300, svg.parentElement.clientWidth), estrecho = W < 620;
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

function pintarLocalidades(locs) {
  const caja = $("#g-localidades");
  if (!locs.length) { caja.innerHTML = `<p class="como-leer">Ningún aviso tiene localidad asignada.</p>`; return; }
  const max = Math.max(...locs.map((l) => l.mediana_m2));
  caja.innerHTML = locs.map((l) => `<div class="barra-loc${l.n < 10 ? " barra-loc--pocos" : ""}">
      <span class="barra-loc__nombre" title="${l.localidad}">${l.localidad} <small>(${fmt.format(l.n)})</small></span>
      <span class="barra-loc__pista"><span class="barra-loc__relleno" style="width:${(l.mediana_m2 / max) * 100}%"></span></span>
      <span class="barra-loc__valor">${m2(l.mediana_m2)}</span></div>`).join("");
}

function colorM2(t) {
  // interpolación de verde claro a ladrillo pasando por musgo
  const a = [201, 216, 196], b = [79, 107, 91], c = [180, 71, 44];
  const [p, q, u] = t < 0.5 ? [a, b, t * 2] : [b, c, (t - 0.5) * 2];
  return `rgb(${p.map((v, i) => Math.round(v + (q[i] - v) * u)).join(",")})`;
}

function pintarMapa(puntos) {
  const caja = $("#mapa");
  if (!puntos.length) { caja.innerHTML = `<div class="mapa__falla">Los avisos de este dataset no tienen coordenadas.</div>`; return; }
  if (!window.L) {
    // Leaflet se carga con defer; si no llegó (sin internet), se avisa en vez de dejar el hueco
    if (!pintarMapa.reintento) { pintarMapa.reintento = true; return addEventListener("load", () => pintarMapa(puntos)); }
    caja.innerHTML = `<div class="mapa__falla">El mapa necesita conexión a internet para cargar las calles.</div>`; return;
  }
  const mapa = L.map(caja, { scrollWheelZoom: false }).setView([4.65, -74.09], 11);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; colaboradores de <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    maxZoom: 18,
  }).addTo(mapa);
  const valores = puntos.map((p) => p.m2).sort((a, b) => a - b);
  const lo = valores[Math.floor(valores.length * 0.05)], hi = valores[Math.floor(valores.length * 0.95)];
  const capa = [];
  for (const p of puntos) {
    const t = Math.min(1, Math.max(0, (p.m2 - lo) / (hi - lo || 1)));
    capa.push(L.circleMarker([p.lat, p.lon], { radius: 5, weight: 1, color: "#fff", fillColor: colorM2(t), fillOpacity: 0.9 })
      .bindPopup(`<strong>${corto(p.precio)}</strong><br>${area(p.area)}, ${m2(p.m2)} por m²${p.localidad ? `<br>${p.localidad}` : ""}`));
  }
  const grupo = L.featureGroup(capa).addTo(mapa);
  mapa.fitBounds(grupo.getBounds(), { padding: [20, 20], maxZoom: 13 });
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
      <td>${corto(a.precio)}</td><td>${m2(a.precio_m2)}</td><td>${area(a.area_m2)}</td>
      <td>${a.habitaciones ?? "—"}</td><td>${a.banos ?? "—"}</td><td>${chipEstrato(a.estrato)}</td>
      <td>${a.localidad ?? "—"}</td><td>${a.barrio ?? "—"}</td><td>${enlaceAviso(a.url_origen)}</td></tr>`).join("");
  $("#a-pagina").textContent = `Página ${r.pagina} de ${paginas}`;
  $("#a-limpiar").hidden = !["localidad", "estrato", "precio_max", "area_min"].some((k) => f.get(k));
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
    ? `El ${elegido.toLowerCase()} le gana a la línea base, así que es el que responde las estimaciones.`
    : `Ninguno de los tres modelos de árboles superó a la línea base, así que la aplicación usa la línea base. Es la decisión que el proyecto fijó de antemano: un modelo más complejo solo se usa si mejora el resultado.`;
  const advertencia = d.tipo === "demo"
    ? `<p><strong>Estas métricas salen de datos sintéticos</strong> y no dicen nada sobre el mercado real.</p>`
    : d.avisos < 300 ? `<p><strong>Con ${fmt.format(d.avisos)} avisos estas métricas son inestables.</strong> El proyecto se propuso reunir entre 500 y 1.500 antes de sacar conclusiones.</p>` : "";
  $("#mo-texto").innerHTML = `
    <p class="veredicto">${veredicto}</p>
    <p>La línea base es la cuenta que haría cualquiera con una hoja de cálculo: el precio mediano del metro cuadrado en la misma localidad y estrato, multiplicado por el área. Frente a ella se comparan un árbol de decisión, un bosque aleatorio y un modelo de <i>gradient boosting</i>. Los árboles aprenden el logaritmo del precio para que los apartamentos más caros no dominen.</p>
    <p>El rango de precio se calcula con los errores que el modelo comete en avisos que no vio (predicción conformal): debería contener el precio real ${Math.round(m.rango.nivel * 10)} de cada 10 veces.</p>
    ${advertencia}
    <p>Entrenado el ${new Date(m.fecha).toLocaleString("es-CO", { dateStyle: "long", timeStyle: "short" })} con <code>${d.archivo}</code>: ${fmt.format(d.entrenamiento)} avisos para entrenar y ${fmt.format(d.prueba)} reservados para la prueba final. Para volver a entrenar: <code>python -m modelo.entrenar</code>.</p>`;
  $("#mo-cv").innerHTML = m.validacion_cruzada.resultados.map((r) => `<tr class="${r.modelo === elegido ? "elegido" : ""}">
      <td>${r.modelo}</td><td class="num">${corto(r.mae)}</td><td class="num">${fmt1.format(r.error_relativo * 100)} %</td>
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
for (const id of ["estimar", "mercado", "avisos", "modelo"]) espia.observe(document.getElementById(id));

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
