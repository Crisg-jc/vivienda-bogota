"""Regresión de la interfaz en un navegador real: cada flujo que ya se rompió o que se pidió alguna vez."""
import os
import re

import pytest

INICIO = "/?area=70&estrato=4&localidad=Suba&habitaciones=3&banos=2&parqueaderos=1"


def abrir(pg, ruta=INICIO):
    pg.goto(pg.base + ruta, wait_until="networkidle")
    pg.wait_for_function("document.querySelector('#r-viabilidad') && document.querySelector('#r-viabilidad').textContent.length > 10")


def test_estimacion_inicial_con_confiabilidad_y_explicacion(pagina):
    abrir(pagina)
    assert re.search(r"Confiabilidad (alta|media|baja)", pagina.inner_text("#r-viabilidad"))
    assert re.match(r"\$[\d.]+ millones", pagina.inner_text("#r-precio"))
    exp = pagina.inner_text("#r-explicacion")
    for parte in ("Apartamentos encontrados", "Usados como referencia", "Búsquedas", "De dónde vienen", "Precio final"):
        assert parte in exp
    assert "avisos" in pagina.inner_text("#insignia-texto")


@pytest.mark.parametrize("ancho,alto", [(1528, 740), (1366, 768), (390, 844)])
def test_formulario_completo_sin_bajar_y_sin_desborde(pagina, ancho, alto):
    pagina.set_viewport_size({"width": ancho, "height": alto})
    abrir(pagina)
    abajo = pagina.evaluate("document.querySelector('.contadores').getBoundingClientRect().bottom")
    assert abajo <= alto, f"los contadores terminan en {abajo} px con {alto} px visibles"
    assert pagina.evaluate("document.documentElement.scrollWidth") <= ancho, "desborde horizontal"


def test_cambiar_estrato_recalcula_y_actualiza_la_direccion(pagina):
    abrir(pagina)
    antes = pagina.inner_text("#r-precio")
    pagina.click("#estratos button:nth-child(6)")
    pagina.wait_for_function(f"document.querySelector('#r-precio').textContent !== {antes!r}")
    assert "estrato=6" in pagina.url


def test_sin_comparables_no_hay_precio_inventado(pagina):
    """Regresión: el app.js viejo dibujaba '$0 M' y la cinta con NaN."""
    abrir(pagina)
    pagina.fill("#area", "950")
    pagina.wait_for_function("document.querySelector('#r-viabilidad').dataset.nivel === 'insuficiente'")
    assert pagina.is_hidden("#r-con-precio")
    assert "$0" not in pagina.inner_text("#resultado")
    assert "Sin información suficiente" in pagina.inner_text("#r-viabilidad")


def test_perfil_de_la_zona_adapta_el_formulario(pagina):
    abrir(pagina)
    assert "avisos reales" in pagina.inner_text("#pz-titulo")
    conteos = pagina.eval_on_selector_all("#estratos .segmento__n", "e => e.map(x => x.textContent)")
    assert any(c not in ("", "0") for c in conteos)
    assert pagina.inner_text("#p-habitaciones").startswith("Común")
    pagina.click("#perfil-zona summary")
    assert "Tipos más publicados" in pagina.inner_text("#pz-cuerpo")


def test_mas_detalles_y_ubicacion_llegan_a_la_api(pagina):
    abrir(pagina)
    with pagina.expect_request(lambda r: r.url.endswith("/api/estimar") and '"piso":7' in (r.post_data or "")) as req:
        pagina.click("#mas-detalles summary")
        pagina.fill("#piso", "7")
    cuerpo = req.value.post_data
    assert '"latitud"' in cuerpo and '"longitud"' in cuerpo, "el punto de la localidad debe enviarse"


def test_zonas_sincronizada_con_la_estimacion(pagina):
    abrir(pagina)
    pagina.select_option("#localidad", "Kennedy")
    pagina.wait_for_function("document.querySelector('#z-nombre').textContent === 'Kennedy'")
    pagina.wait_for_function("document.querySelector('#z-tendencia').textContent.includes('Cambios de precio')")


def test_avisos_filtros_e_historial(pagina):
    abrir(pagina, "/#avisos")
    pagina.wait_for_selector("#a-filas tr")
    pagina.select_option("#f-localidad", "Suba")
    pagina.wait_for_function("[...document.querySelectorAll('#a-filas tr')].every(t => t.textContent.includes('Suba'))")
    assert "Nan" not in pagina.inner_text("#a-filas"), "regresión: barrio vacío mostrado como 'Nan'"


def test_modelo_muestra_precision_medida(pagina):
    abrir(pagina, "/#modelo")
    pagina.wait_for_selector("#mo-precision table")
    assert "¿Cuánto aciertan de verdad?" in pagina.inner_text("#mo-precision")


def test_comparar_dos_apartamentos(pagina):
    abrir(pagina)
    pagina.click("#fijar-comparar")
    pagina.click("#estratos button:nth-child(5)")
    pagina.wait_for_selector("#comparacion:not([hidden]) table")
    assert re.search(r"(más|menos) que el fijado|prácticamente lo mismo|falta información", pagina.inner_text("#comparacion"))


def test_modo_oscuro_se_recuerda(pagina):
    abrir(pagina)
    pagina.click("#cambiar-tema")
    pagina.wait_for_load_state("networkidle")
    assert pagina.evaluate("document.documentElement.dataset.temaEfectivo") == "oscuro"
    fondo = pagina.evaluate("getComputedStyle(document.body).backgroundColor")
    assert fondo != "rgb(238, 241, 238)"
    pagina.reload(wait_until="networkidle")
    assert pagina.evaluate("document.documentElement.dataset.temaEfectivo") == "oscuro"


def test_hoja_de_impresion(pagina):
    abrir(pagina)
    pagina.evaluate("window.dispatchEvent(new Event('beforeprint'))")
    pagina.emulate_media(media="print")
    assert pagina.is_visible("#impresion") and "Estimación del precio de publicación" in pagina.inner_text("#impresion")
    assert pagina.is_hidden(".formulario") and pagina.is_hidden("#zonas")


def test_accesibilidad_sin_violaciones_graves(pagina):
    ruta = os.environ.get("AXE_PATH")
    if not ruta or not os.path.exists(ruta):
        pytest.skip("axe-core no disponible (AXE_PATH)")
    abrir(pagina)
    pagina.add_script_tag(path=ruta)
    r = pagina.evaluate("axe.run(document, {resultTypes: ['violations']})")
    graves = [f"{v['id']}: {v['help']} ({len(v['nodes'])})" for v in r["violations"] if v["impact"] in ("serious", "critical")]
    assert graves == []
    # También con el panel de detalle abierto (diálogo con mapa y acciones)
    pagina.wait_for_selector("#a-filas tr.fila-aviso")
    pagina.click("#a-filas tr.fila-aviso .enlace-detalle")
    pagina.wait_for_selector("#panel:not([hidden]) .ficha")
    r = pagina.evaluate("axe.run(document, {resultTypes: ['violations']})")
    graves = [f"{v['id']}: {v['help']} ({len(v['nodes'])})" for v in r["violations"] if v["impact"] in ("serious", "critical")]
    assert graves == []


# ---------------------------------------------------------------- rediseño: navegación, tabla, panel, panorama

@pytest.mark.parametrize("ancho,lateral", [(1440, True), (1024, False), (390, False)])
def test_navegacion_lateral_o_superior_sin_desborde(pagina, ancho, lateral):
    pagina.set_viewport_size({"width": ancho, "height": 800})
    abrir(pagina)
    caja = pagina.evaluate("(() => { const r = document.querySelector('.barra').getBoundingClientRect(); return [r.width, r.height]; })()")
    assert (caja[1] > 600) == lateral, f"barra {caja} en {ancho} px"
    assert pagina.evaluate("document.documentElement.scrollWidth") <= ancho
    pagina.click(".barra__nav a[href='#avisos']")
    pagina.wait_for_function("document.querySelector(\".barra__nav a[href='#avisos']\").getAttribute('aria-current') === 'true'")


def test_tabla_de_avisos_ordena_filtra_con_chips_y_pagina(pagina):
    abrir(pagina, "/#avisos")
    pagina.wait_for_selector("#a-filas tr.fila-aviso")
    pagina.click("button.orden[data-orden='precio_m2']")
    pagina.wait_for_function("document.querySelector('th[data-col=m2]').getAttribute('aria-sort') === 'ascending'")
    pagina.click("button.orden[data-orden='precio_m2']")
    pagina.wait_for_function("document.querySelector('th[data-col=m2]').getAttribute('aria-sort') === 'descending'")
    assert pagina.input_value("#f-orden") == "-precio_m2"
    pagina.select_option("#f-localidad", "Suba")
    pagina.wait_for_selector("#a-chips [data-quitar]")
    assert "Suba" in pagina.inner_text("#a-chips")
    pagina.click("#a-chips [data-quitar='localidad']")
    pagina.wait_for_function("document.querySelector('#f-localidad').value === ''")
    pagina.select_option("#a-por-pagina", "50")
    pagina.wait_for_function("document.querySelectorAll('#a-filas tr.fila-aviso').length > 20")


def test_columnas_se_ocultan_y_se_recuerdan(pagina):
    abrir(pagina, "/#avisos")
    pagina.wait_for_selector("#a-filas tr.fila-aviso")
    pagina.click("#menu-columnas summary")
    pagina.uncheck("#menu-columnas-lista input[value='barrio']")
    assert pagina.is_hidden("#tabla-avisos th[data-col='barrio']")
    pagina.reload(wait_until="networkidle")
    pagina.wait_for_selector("#a-filas tr.fila-aviso")
    assert pagina.is_hidden("#tabla-avisos th[data-col='barrio']")


def test_exportar_csv_descarga_los_avisos_filtrados(pagina):
    abrir(pagina, "/#avisos")
    pagina.wait_for_selector("#a-filas tr.fila-aviso")
    with pagina.expect_download() as d:
        pagina.click("#a-exportar")
    contenido = open(d.value.path(), encoding="utf-8-sig").read().splitlines()
    assert contenido[0].startswith("Precio;") and len(contenido) > 20
    pagina.wait_for_selector(".toast")


def test_detalle_del_aviso_en_panel_accesible(pagina):
    abrir(pagina, "/#avisos")
    pagina.wait_for_selector("#a-filas tr.fila-aviso")
    pagina.click("#a-filas tr.fila-aviso .enlace-detalle")
    pagina.wait_for_selector("#panel:not([hidden]) .ficha")
    assert pagina.evaluate("document.activeElement.closest('#panel') !== null"), "el foco debe entrar al panel"
    assert pagina.get_attribute("#panel", "aria-modal") == "true"
    pagina.keyboard.press("Escape")
    pagina.wait_for_selector("#panel", state="hidden")
    assert pagina.evaluate("document.activeElement.classList.contains('enlace-detalle')"), "el foco vuelve a la fila"


def test_comparable_abre_su_detalle(pagina):
    abrir(pagina)
    pagina.click("#r-similares [data-ficha]")
    pagina.wait_for_selector("#panel:not([hidden]) .ficha")
    pagina.click("#panel [data-accion='estimar']")
    pagina.wait_for_selector("#panel", state="hidden")


def test_tabla_de_avisos_en_celular_son_tarjetas(pagina):
    pagina.set_viewport_size({"width": 390, "height": 844})
    abrir(pagina, "/#avisos")
    pagina.wait_for_selector("#a-filas tr.fila-aviso")
    assert pagina.is_hidden("#filtros"), "en celular los filtros empiezan plegados"
    pagina.click("#f-abrir")
    assert pagina.is_visible("#filtros")
    assert pagina.evaluate("getComputedStyle(document.querySelector('#a-filas tr')).display") != "table-row"
    assert pagina.evaluate("document.documentElement.scrollWidth") <= 390


def test_panorama_muestra_indicadores_reales_sin_inventar_variaciones(pagina):
    abrir(pagina, "/#mercado")
    pagina.wait_for_selector("#m-kpis .kpi")
    texto = pagina.inner_text("#m-kpis")
    assert "Metro cuadrado mediano" in texto and "Avisos nuevos" in texto
    # El servidor de prueba no tiene historia en la base: se dice, no se inventa un porcentaje.
    assert "Sin historia para comparar" in texto
    assert not re.search(r"[▲▼] \d", texto)
