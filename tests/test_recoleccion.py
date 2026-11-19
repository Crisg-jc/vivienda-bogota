"""Simula un portal con páginas FICTICIAS para probar listado -> detalle -> dataset."""
import json

import pandas as pd
import pytest

from scraper import recolectar as rc

VACIA = '<p>sin resultados</p>'
LISTADO = {
    # página 1: solo proyectos destacados (como Finca Raíz real); no debe detener el listado
    1: '<a href="/proyectos-vivienda/x-apartamentos-en-venta-en-y-bogota/9">p</a>',
    2: '<a href="/apartamento-en-venta-en-niza-bogota/111">a</a><a href="/apartamento-en-venta-en-cedritos-bogota/222">b</a>',
    3: '<a href="/apartamento-en-venta-en-niza-bogota/111">repetido</a>',
    4: VACIA, 5: VACIA, 6: VACIA, 7: VACIA, 8: VACIA, 9: VACIA,
}


def detalle(precio, area, loc):
    d = {"props": {"pageProps": {"data": {"price": precio, "m2": area, "rooms": 3, "baths": 2,
                                          "stratum": 4, "garage": 1, "location": {"localidad": loc}}}}}
    return f'<script id="__NEXT_DATA__">{json.dumps(d)}</script>'


DETALLES = {"111": detalle(410000000, 68, "Suba"), "222": detalle(530000000, 85, "Usaquén")}


class ClienteFalso:
    def __init__(self, conf): pass
    def get(self, url, base):
        if "pagina" in url:
            return LISTADO[int(url.rsplit("pagina", 1)[1])]
        if url.endswith("bogota-dc"):
            return LISTADO[1]
        return DETALLES[url.rsplit("/", 1)[1]]


def test_flujo(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr(rc, "Cliente", ClienteFalso)
    conf = rc.cargar_conf(); conf["portales"]["fincaraiz"]["terminos_revisados"] = False
    with pytest.raises(SystemExit, match="términos"):
        rc.cmd_listado(type("A", (), {"portal": "fincaraiz", "paginas": 5, "desde": 1}), conf)
    with pytest.raises(SystemExit, match="deshabilitado"):
        rc.portal_autorizado(conf, "metrocuadrado")
    conf["portales"]["fincaraiz"]["terminos_revisados"] = True
    rc.cmd_listado(type("A", (), {"portal": "fincaraiz", "paginas": 10, "desde": 1}), conf)
    rc.cmd_detalle(type("A", (), {"portal": "fincaraiz", "max": 50}), conf)
    from limpieza import construir_dataset as cd
    monkeypatch.setattr(cd, "RAIZ", tmp_path)
    cd.main([])
    df = pd.read_csv(tmp_path / "datos" / "dataset_v1.csv")
    assert len(df) == 2
    assert set(df.localidad) == {"Suba", "Usaquén"}
    assert set(df.barrio) == {"Niza", "Cedritos"}


def test_config_local_se_crea_del_ejemplo_y_no_se_pisa(tmp_path, monkeypatch):
    """El zip trae solo el ejemplo: la configuración de cada equipo (autorización, correo) nunca se reemplaza."""
    ejemplo, config = tmp_path / "portales.ejemplo.yaml", tmp_path / "portales.yaml"
    ejemplo.write_text("general: {}\nportales: {fincaraiz: {terminos_revisados: false}}\n", encoding="utf-8")
    monkeypatch.setattr(rc, "EJEMPLO", ejemplo)
    monkeypatch.setattr(rc, "CONFIG", config)
    assert rc.cargar_conf()["portales"]["fincaraiz"]["terminos_revisados"] is False
    config.write_text("general: {}\nportales: {fincaraiz: {terminos_revisados: true}}\n", encoding="utf-8")
    assert rc.cargar_conf()["portales"]["fincaraiz"]["terminos_revisados"] is True
