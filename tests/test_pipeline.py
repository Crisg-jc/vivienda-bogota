"""Pruebas con páginas HTML FICTICIAS. No son datos del mercado: solo verifican el código."""
import json
import os
import pathlib

import pandas as pd
import pytest

from scraper.extraer import enlaces_detalle, extraer
from scraper.http import Robots
from limpieza import normalizar as nz

ROBOTS_FR = """
User-agent: AhrefsBot
Disallow: /
User-agent: *
Disallow: /venta/*-y-*
Disallow: /apps/
"""


def test_robots_comodines():
    r = Robots(ROBOTS_FR, "ProyectoAcademicoECCI-PrecioVivienda/1.0")
    assert r.permitido("https://x.co/venta/apartamentos/bogota/bogota-dc/pagina2")
    assert not r.permitido("https://x.co/venta/apartamentos-y-casas/bogota")
    assert not r.permitido("https://x.co/apps/algo")
    assert not Robots(ROBOTS_FR, "AhrefsBot").permitido("https://x.co/cualquier")


def test_robots_allow_mas_especifico():
    r = Robots("User-agent: *\nDisallow: /detail/\nAllow: /detail/publico$", "bot")
    assert not r.permitido("https://x.co/detail/123")
    assert r.permitido("https://x.co/detail/publico")


PAGINA_NEXT = """<html><body><h1>Apartamento en venta</h1>
<script id="__NEXT_DATA__" type="application/json">{json}</script>
<p>Contacto: Pedro Pérez 3001234567</p></body></html>"""


def test_extraccion_next_data_ignora_similares_y_contacto():
    datos = {"props": {"pageProps": {
        "data": {"price": {"amount": 420000000}, "m2Built": 72.5, "rooms": {"name": "3"},
                 "baths": 2, "stratum": {"name": "Estrato 4"}, "garage": 1,
                 "location": {"localidad": {"name": "Suba"}, "barrio": {"name": "Niza"}},
                 "contact": {"phone": "3001234567", "name": "Pedro"}},
        "similares": [{"price": 900000000, "m2": 150}]}}}
    html = PAGINA_NEXT.format(json=json.dumps(datos))
    campos, metodo = extraer(html, "https://www.fincaraiz.com.co/apartamento-en-venta-en-niza-bogota/1")
    assert campos["precio"] == "420000000"
    assert campos["area"] == "72.5"
    assert campos["localidad"] == "Suba" and campos["barrio"] == "Niza"
    assert "next-data" in metodo
    assert "3001234567" not in json.dumps(campos)


def test_extraccion_json_ld_y_texto():
    ld = {"@type": "Apartment", "floorSize": {"value": 60}, "numberOfBedrooms": 2,
          "offers": {"price": 280000000}}
    html = (f'<script type="application/ld+json">{json.dumps(ld)}</script>'
            "<div>Estrato 3</div><div>1 parqueadero</div><div>Administración: $ 250.000</div>")
    url = "https://www.ciencuadras.com/inmueble/apartamento-en-venta-en-bellavista-bogota-3832308"
    campos, metodo = extraer(html, url, r"/inmueble/apartamento-en-venta-en-([a-z0-9-]+)-bogota-\d+")
    assert campos["precio"] == "280000000" and campos["habitaciones"] == "2"
    assert campos["estrato"] == "3" and campos["parqueaderos"] == "1"
    assert campos["administracion"] == "250.000"
    assert campos["barrio"] == "bellavista"
    assert metodo == "json-ld+texto"


def test_enlaces_detalle_excluye_proyectos():
    html = """<a href="/apartamento-en-venta-en-la-calleja-bogota/194194812">a</a>
              <a href="/proyectos-vivienda/maui-68-apartamentos-en-venta-en-venecia-bogota/10471287">p</a>
              <a href="/apartamento-en-venta-en-la-calleja-bogota/194194812?utm=x">dup</a>"""
    pat = r'^https://www\.fincaraiz\.com\.co/apartamento-en-venta-en-[a-z0-9-]+-bogota/\d+/?$'
    assert enlaces_detalle(html, "https://www.fincaraiz.com.co", pat) == [
        "https://www.fincaraiz.com.co/apartamento-en-venta-en-la-calleja-bogota/194194812"]


@pytest.mark.parametrize("txt,esperado", [
    ("$ 350.000.000", 350_000_000), ("350000000.0", 350_000_000), ("$350'000.000", 350_000_000),
    ("350 millones", 350_000_000), ("$ 1.250 millones", 1_250_000_000),
    ("$ 420.000.000,00", 420_000_000), (None, None)])
def test_precio(txt, esperado):
    assert nz.precio(txt) == esperado


def test_area_localidad_antiguedad():
    assert nz.area("72,5 m²") == 72.5 and nz.area("1.250 m2") == 1250
    assert nz.localidad("Localidad de Usaquén, Bogotá") == "Usaquén"
    assert nz.localidad("ENGATIVA") == "Engativá"
    assert nz.localidad("Bogotá") is None
    assert nz.antiguedad("Entre 10 y 20 años") == 15
    assert nz.antiguedad("Para estrenar") == 0
    import datetime as dt
    assert nz.antiguedad("2006", dt.date(2026, 1, 1)) == 20
    assert not nz.es_apartamento("Casa") and nz.es_apartamento("Apartamento")


def test_construccion_dataset_completa(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    from scraper import db
    from limpieza import construir_dataset as cd
    monkeypatch.setattr(cd, "RAIZ", tmp_path)
    eng = db.motor()
    import datetime as dt
    base = dict(habitaciones_txt="3", banos_txt="2", estrato_txt="4", parqueaderos_txt="1",
                antiguedad_txt=None, administracion_txt=None, barrio_txt="Niza",
                localidad_txt="Suba", tipo_txt="Apartamento", metodo_extraccion="test")
    filas = [
        dict(base, portal="fincaraiz", url_origen="u1", precio_txt="$ 400.000.000", area_txt="70 m²",
             fecha_consulta=dt.date(2026, 10, 1)),
        # mismo inmueble en otro portal: área +0,5 m² y precio +1 % -> duplicado
        dict(base, portal="ciencuadras", url_origen="u2", precio_txt="404000000", area_txt="70,5",
             fecha_consulta=dt.date(2026, 10, 2)),
        dict(base, portal="fincaraiz", url_origen="u3", precio_txt="$ 600.000.000", area_txt="95",
             fecha_consulta=dt.date(2026, 10, 1)),
        dict(base, portal="fincaraiz", url_origen="u4", precio_txt=None, area_txt="80",
             fecha_consulta=dt.date(2026, 10, 1)),
        dict(base, portal="fincaraiz", url_origen="u5", precio_txt="$ 500.000.000", area_txt="900",
             fecha_consulta=dt.date(2026, 10, 1)),
        dict(base, portal="fincaraiz", url_origen="u6", precio_txt="$ 500.000.000", area_txt="80",
             tipo_txt="Casa", fecha_consulta=dt.date(2026, 10, 1)),
    ]
    with eng.begin() as c:
        c.execute(db.inmuebles_crudos.insert(), filas)
    cd.main(["--notas", "prueba"])
    out = pd.read_csv(tmp_path / "datos" / "dataset_v1.csv")
    assert sorted(out.url_origen) == ["u1", "u3"]
    rep = (tmp_path / "datos" / "reporte_calidad_v1.md").read_text(encoding="utf-8")
    assert "Duplicados eliminados: 1" in rep and "sin precio o sin área | 1" in rep
    with eng.begin() as c:
        v = c.execute(db.dataset_versiones.select()).mappings().one()
        assert v["n_registros"] == 2 and len(v["checksum_sha256"]) == 64


def test_lector_fincaraiz_estructura_real():
    """Misma estructura que un aviso real de Finca Raíz (2026-10-04), con valores FICTICIOS."""
    data = {"price": {"amount": 450000000, "admin_included": 450500000}, "m2": 80, "m2Built": 80,
            "m2apto": 76, "rooms": 0, "bedrooms": 3, "bathrooms": 2, "stratum": 4, "garage": 1,
            "floor": 6, "latitude": 4.70, "longitude": -74.05, "isProject": False,
            "commonExpenses": {"amount": 400000}, "property_type": {"id": 2, "name": "Apartamento"},
            "owner": {"name": "Inmobiliaria X", "masked_phone": "+5730"},
            "address": "CL 1 # 2 - 3",
            "technicalSheet": [{"field": "constructionYear", "value": "1 a 8 años"},
                               {"field": "construction_state_name", "value": "Usado"}],
            "locations": {"locality": [{"name": "Usaquen"}], "neighbourhood": [{"name": "Cedritos"}]}}
    ld = {"@type": "BreadcrumbList", "itemListElement": []}
    html = (f'<script type="application/ld+json">{json.dumps(ld)}</script>'
            f'<script id="__NEXT_DATA__">{json.dumps({"props": {"pageProps": {"data": data}}})}</script>')
    campos, metodo = extraer(html, "https://www.fincaraiz.com.co/apartamento-en-venta-en-cedritos-bogota/1")
    assert metodo.startswith("fincaraiz-json")
    assert campos["habitaciones"] == "3"          # bedrooms, no rooms (ambientes)
    assert campos["localidad"] == "Usaquen" and campos["barrio"] == "Cedritos"
    assert campos["tipo"] == "Apartamento"        # no 'BreadcrumbList'
    assert campos["administracion"] == "400000" and campos["piso"] == "6"
    texto = json.dumps(campos)
    assert "Inmobiliaria" not in texto and "+5730" not in texto and "CL 1" not in texto
    data["isProject"] = True
    html2 = f'<script id="__NEXT_DATA__">{json.dumps({"props": {"pageProps": {"data": data}}})}</script>'
    campos2, _ = extraer(html2, "x")
    assert not nz.es_apartamento(campos2["tipo"])  # los proyectos se descartan en la limpieza


def test_enlaces_dentro_de_json():
    """Tarjetas armadas con JavaScript: la ruta del aviso solo está en el JSON interno."""
    html = ('<div class="card"></div><script id="__NEXT_DATA__">{"items":[{"link":'
            '"/apartamento-en-venta-en-cedritos-bogota/123456"},{"link":"\\/apartamento-en-venta-en-'
            'niza-bogota\\/654321"},{"link":"/proyectos-vivienda/x-apartamentos-en-venta-en-y-bogota/1"}]}'
            '</script>')
    pat = r'^https://www\.fincaraiz\.com\.co/apartamento-en-venta-en-[a-z0-9-]+-bogota/\d+/?$'
    assert enlaces_detalle(html, "https://www.fincaraiz.com.co", pat) == [
        "https://www.fincaraiz.com.co/apartamento-en-venta-en-cedritos-bogota/123456",
        "https://www.fincaraiz.com.co/apartamento-en-venta-en-niza-bogota/654321"]


def test_localidad_por_coordenadas_y_admin(tmp_path, monkeypatch):
    from limpieza import geo
    from limpieza.construir_dataset import admin_valida
    # Dos "localidades" FICTICIAS cuadradas, con un nombre de campo cualquiera
    cuadro = lambda x0, y0: [[[x0, y0], [x0 + .1, y0], [x0 + .1, y0 + .1], [x0, y0 + .1], [x0, y0]]]
    gj = {"type": "FeatureCollection", "features": [
        {"properties": {"CODIGO": "11", "NOMBRE_X": "ENGATIVA"}, "geometry": {"type": "Polygon", "coordinates": cuadro(-74.15, 4.65)}},
        {"properties": {"LocNombre": "TEUSAQUILLO"}, "geometry": {"type": "MultiPolygon", "coordinates": [cuadro(-74.05, 4.60)]}}]}
    archivo = tmp_path / "loc.geojson"
    archivo.write_text(json.dumps(gj), encoding="utf-8")
    monkeypatch.setattr(geo, "ARCHIVO", archivo)
    monkeypatch.setattr(geo, "_POLIGONOS", None)
    assert geo.localidad_por_coordenadas(4.70, -74.10) == "Engativá"
    assert geo.localidad_por_coordenadas(4.63, -74.00) == "Teusaquillo"
    assert geo.localidad_por_coordenadas(4.90, -74.10) is None
    assert admin_valida(0, 500_000_000) is None
    assert admin_valida(460_000_000, 580_000_000) is None
    assert admin_valida(460_000, 580_000_000) == 460_000


def test_mapa_formato_esri(tmp_path, monkeypatch):
    """Mapa en Esri JSON (ArcGIS): 'attributes' + 'rings', con un hueco. Valores FICTICIOS."""
    from limpieza import geo
    exterior = [[-74.2, 4.6], [-74.2, 4.8], [-74.0, 4.8], [-74.0, 4.6], [-74.2, 4.6]]
    hueco = [[-74.12, 4.68], [-74.08, 4.68], [-74.08, 4.72], [-74.12, 4.72], [-74.12, 4.68]]
    gj = {"spatialReference": {"wkid": 4326}, "features": [
        {"attributes": {"LocCodigo": "11", "LocNombre": "SUBA"}, "geometry": {"rings": [exterior, hueco]}}]}
    archivo = tmp_path / "loc.json"
    archivo.write_text(json.dumps(gj), encoding="utf-8")
    monkeypatch.setattr(geo, "ARCHIVO", archivo)
    monkeypatch.setattr(geo, "_POLIGONOS", None)
    assert geo.localidad_por_coordenadas(4.65, -74.15) == "Suba"
    assert geo.localidad_por_coordenadas(4.70, -74.10) is None     # dentro del hueco
    assert "LocNombre" in geo.estructura()
