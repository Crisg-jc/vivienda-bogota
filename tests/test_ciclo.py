"""Ciclo automático contra un portal FICTICIO que cambia entre un ciclo y el siguiente."""
import json

import pandas as pd
import pytest
from sqlalchemy import select

from scraper import ciclo as cy
from scraper import db
from scraper import recolectar as rc
from scraper.http import AvisoRetirado, BloqueoDelPortal

BASE = "https://www.fincaraiz.com.co"


def detalle(precio, area, loc, estrato=4):
    # Misma estructura que un aviso real de Finca Raíz (props.pageProps.data)
    d = {"props": {"pageProps": {"data": {"price": precio, "m2Built": area, "bedrooms": 3, "bathrooms": 2,
                                          "stratum": estrato, "garage": 1,
                                          "locations": {"locality": [{"name": loc}]}}}}}
    return f'<script id="__NEXT_DATA__">{json.dumps(d)}</script>'


class Portal:
    """Estado del portal ficticio; las pruebas lo modifican entre ciclos."""
    listado = {}
    avisos = {}
    retirados = set()
    bloquear = False


class ClienteFalso:
    def __init__(self, conf):
        pass

    def obtener(self, url, base):
        if Portal.bloquear:
            raise BloqueoDelPortal("429 en " + url)
        if "/apartamento-en-venta-en-" in url:
            clave = url.rsplit("/", 1)[1]
            if clave in Portal.retirados:
                raise AvisoRetirado("404 en " + url)
            return Portal.avisos[clave], url
        n = int(url.rsplit("pagina", 1)[1]) if "pagina" in url else 1
        return Portal.listado.get(n, "<p>sin resultados</p>"), url

    def get(self, url, base):
        return self.obtener(url, base)[0]


def enlace(barrio, clave):
    return f'<a href="/apartamento-en-venta-en-{barrio}-bogota/{clave}">x</a>'


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr(cy, "CARPETA", tmp_path / "estado")
    monkeypatch.setattr(cy, "ESTADO", tmp_path / "estado" / "ciclo.json")
    monkeypatch.setattr(cy, "CANDADO", tmp_path / "estado" / "ciclo.lock")
    monkeypatch.setattr(cy, "RAIZ", tmp_path)
    from limpieza import construir_dataset as cd
    monkeypatch.setattr(cd, "RAIZ", tmp_path)
    Portal.listado = {2: enlace("niza", "111") + enlace("cedritos", "222") + enlace("chico", "333")}
    Portal.avisos = {"111": detalle(410_000_000, 68, "Suba"), "222": detalle(530_000_000, 85, "Usaquén"),
                     "333": detalle(900_000_000, 120, "Chapinero", 6)}
    Portal.retirados, Portal.bloquear = set(), False
    conf = rc.cargar_conf()
    conf["portales"]["fincaraiz"]["terminos_revisados"] = True
    conf["portales"]["ciencuadras"]["habilitado"] = False
    conf["automatico"].update(paginas_iniciales_siempre=3, paginas_por_ciclo=10, dias_entre_revisiones=-1)  # -1: revisar todo en cada ciclo
    conf["general"]["max_paginas_vacias_seguidas"] = 3
    return conf, tmp_path


def correr(conf):
    return cy.ejecutar(conf, cliente_cls=ClienteFalso, entrenar_modelo=False)


def test_primer_ciclo_recolecta_y_construye_dataset(entorno):
    conf, tmp = entorno
    r = correr(conf)
    assert r["resultado"] == "completo"
    assert r["enlaces_nuevos"] == 3 and r["avisos_nuevos"] == 3
    df = pd.read_csv(tmp / "datos" / f"dataset_v{r['version_dataset']}.csv")
    assert len(df) == 3 and set(df.localidad) == {"Suba", "Usaquén", "Chapinero"}
    estado = json.loads((tmp / "estado" / "ciclo.json").read_text(encoding="utf-8"))
    assert estado["estado"] == "inactivo" and estado["ultimo_ciclo"]["resultado"] == "completo"


def test_revision_detecta_cambio_de_precio_retiro_y_aviso_nuevo(entorno):
    conf, tmp = entorno
    correr(conf)
    # Entre ciclos: un aviso baja de precio, otro se retira y aparece uno nuevo (y uno repetido)
    Portal.avisos["111"] = detalle(380_000_000, 68, "Suba")
    Portal.retirados.add("222")
    Portal.listado[2] += enlace("niza", "111") + enlace("modelia", "444")
    Portal.avisos["444"] = detalle(350_000_000, 60, "Fontibón", 3)
    r = correr(conf)
    assert r["enlaces_nuevos"] == 1, "el enlace repetido no debe duplicarse"
    assert r["avisos_nuevos"] == 1 and r["con_cambios"] == 1 and r["retirados"] == 1
    eng = db.motor()
    with eng.begin() as c:
        cambios = c.execute(select(db.cambios)).mappings().all()
        assert {(x["campo"], x["despues"]) for x in cambios} >= {("precio", "380000000"), ("disponible", "no")}
        crudo = c.execute(select(db.inmuebles_crudos).where(
            db.inmuebles_crudos.c.url_origen.like("%/111"))).mappings().one()
        assert crudo["precio_txt"] == "380000000"
        n = c.scalar(select(db.func.count()).select_from(db.inmuebles_crudos))
        assert n == 4, "sin duplicados en la base"
    df = pd.read_csv(tmp / "datos" / f"dataset_v{r['version_dataset']}.csv")
    assert "Usaquén" not in set(df.localidad), "el aviso retirado sale del dataset"
    fila = df[df.url_origen.str.endswith("/111")].iloc[0]
    assert fila.precio == 380_000_000 and fila.precio_anterior == 410_000_000


def test_aviso_que_vuelve_a_publicarse_se_reactiva(entorno):
    conf, _ = entorno
    correr(conf)
    Portal.retirados.add("333")
    correr(conf)
    Portal.retirados.clear()
    with db.motor().begin() as c:   # el enlace retirado no se revisa solo; se reactiva si reaparece en el listado
        c.execute(db.enlaces.update().values(disponible=True))
    correr(conf)
    with db.motor().begin() as c:
        crudo = c.execute(select(db.inmuebles_crudos).where(
            db.inmuebles_crudos.c.url_origen.like("%/333"))).mappings().one()
    assert crudo["disponible"] is True and crudo["retirado_en"] is None


def test_bloqueo_detiene_el_ciclo_sin_perder_lo_guardado(entorno):
    conf, _ = entorno
    correr(conf)
    Portal.bloquear = True
    r = correr(conf)
    assert r["resultado"] == "bloqueado"
    with db.motor().begin() as c:
        assert c.scalar(select(db.func.count()).select_from(db.inmuebles_crudos)) == 3


def test_sin_portales_autorizados_no_recolecta(entorno):
    conf, _ = entorno
    conf["portales"]["fincaraiz"]["terminos_revisados"] = False
    r = correr(conf)
    assert r["resultado"] == "sin_portales"


def test_un_segundo_ciclo_no_arranca_si_hay_uno_en_curso(entorno):
    conf, _ = entorno
    assert cy.tomar_candado(240)
    assert correr(conf)["resultado"] == "en_curso"
    cy.soltar_candado()


def test_sin_cambios_no_crea_version_nueva(entorno):
    conf, _ = entorno
    v1 = correr(conf)["version_dataset"]
    r = correr(conf)            # mismos datos: revisa, pero no hay nada nuevo
    assert v1 and r["version_dataset"] is None


def test_sin_internet_se_informa_sin_tumbar_nada(entorno, monkeypatch):
    import requests
    conf, _ = entorno

    class SinRed(ClienteFalso):
        def obtener(self, url, base):
            raise requests.exceptions.ConnectionError("sin red")
    r = cy.ejecutar(conf, cliente_cls=SinRed, entrenar_modelo=False)
    assert r["resultado"] == "sin_conexion"
