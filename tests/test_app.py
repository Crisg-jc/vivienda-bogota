"""Pruebas del modelo y de la API con datos sintéticos en una carpeta temporal."""
import importlib
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def cliente(tmp_path_factory):
    raiz = tmp_path_factory.mktemp("raiz")
    mp = pytest.MonkeyPatch()
    from modelo import datos as D
    from modelo import demo, entrenar
    mp.setattr(D, "DEMO", raiz / "datos" / "demo" / "dataset_demo.csv")
    mp.setattr(D, "RAIZ", raiz)
    mp.setattr(demo, "DEMO", D.DEMO)
    mp.setattr(entrenar, "ARTEFACTOS", raiz / "artefactos")
    mp.setattr(entrenar, "MODELO", raiz / "artefactos" / "modelo.joblib")
    mp.setattr(entrenar, "METRICAS", raiz / "artefactos" / "metricas.json")
    mp.delenv("VIVIENDA_DATASET", raising=False)
    mp.setenv("VIVIENDA_AUTOMATICO", "0")   # las pruebas nunca visitan portales
    from modelo import estimar
    mp.setattr(estimar, "MODELO", entrenar.MODELO)
    import app.main as principal
    importlib.reload(principal)
    with TestClient(principal.app) as c:   # el arranque crea los datos demo y entrena
        yield c, raiz
    mp.undo()


def test_arranque_crea_demo_y_entrena(cliente):
    c, raiz = cliente
    estado = c.get("/api/estado").json()
    assert estado["dataset"]["tipo"] == "demo"
    assert estado["dataset"]["avisos"] > 100
    assert (raiz / "artefactos" / "modelo.joblib").exists()
    m = json.loads((raiz / "artefactos" / "metricas.json").read_text(encoding="utf-8"))
    nombres = {r["modelo"] for r in m["validacion_cruzada"]["resultados"]}
    assert nombres == {"Línea base", "Árbol de decisión", "Bosque aleatorio", "Gradient boosting"}
    # la regla de decisión: el elegido es el de menor MAE en validación cruzada
    mejor = min(m["validacion_cruzada"]["resultados"], key=lambda r: r["mae"])
    assert m["modelo_elegido"] == mejor["modelo"]


def test_estimar_devuelve_rango_y_parecidos(cliente):
    c, _ = cliente
    r = c.post("/api/estimar", json={"area_m2": 70, "estrato": 4, "localidad": "Suba",
                                     "habitaciones": 3, "banos": 2, "parqueaderos": 1})
    assert r.status_code == 200
    d = r.json()
    assert d["minimo"] <= d["precio"] <= d["maximo"]
    assert d["precio"] > 0 and len(d["similares"]) == 6
    assert d["demo"] is True


def test_estimar_mayor_estrato_no_cuesta_menos(cliente):
    c, _ = cliente
    bajo = c.post("/api/estimar", json={"area_m2": 80, "estrato": 2}).json()["precio"]
    alto = c.post("/api/estimar", json={"area_m2": 80, "estrato": 6}).json()["precio"]
    assert alto > bajo


def test_estimar_valida_entradas(cliente):
    c, _ = cliente
    assert c.post("/api/estimar", json={"area_m2": 5, "estrato": 3}).status_code == 422
    assert c.post("/api/estimar", json={"area_m2": 60, "estrato": 9}).status_code == 422
    assert c.post("/api/estimar", json={"area_m2": 60, "estrato": 3, "localidad": "Narnia"}).status_code == 422


def test_estimar_advierte_fuera_de_rango(cliente):
    c, _ = cliente
    d = c.post("/api/estimar", json={"area_m2": 950, "estrato": 3}).json()
    assert any("fuera de lo que el modelo" in a for a in d["advertencias"])


def test_mercado_y_avisos(cliente):
    c, _ = cliente
    m = c.get("/api/mercado").json()
    assert m["estratos"] and all(e["q1"] <= e["mediana"] <= e["q3"] for e in m["estratos"])
    a = c.get("/api/avisos", params={"estrato": 3, "orden": "-precio", "por_pagina": 10}).json()
    precios = [x["precio"] for x in a["avisos"]]
    assert precios == sorted(precios, reverse=True)
    assert all(x["estrato"] == 3 for x in a["avisos"])


def test_interfaz_se_sirve(cliente):
    c, _ = cliente
    r = c.get("/")
    assert r.status_code == 200 and "Precio de publicación estimado" in r.text
    assert c.get("/static/app.js").status_code == 200


def test_linea_base_usa_respaldo_por_estrato():
    import pandas as pd

    from modelo.linea_base import LineaBase
    X = pd.DataFrame({"localidad": ["A"] * 3 + ["B"] * 3, "estrato": [3] * 6, "area_m2": [50.0] * 6})
    y = [150e6, 150e6, 150e6, 250e6, 250e6, 250e6]
    lb = LineaBase().fit(X, y)
    nuevo = pd.DataFrame({"localidad": ["A", "C"], "estrato": [3, 3], "area_m2": [100.0, 100.0]})
    p = lb.predict(nuevo)
    assert p[0] == pytest.approx(300e6)          # localidad y estrato conocidos
    assert p[1] == pytest.approx(400e6)          # localidad nueva: mediana del estrato (4 M/m²)


def test_cabeceras_de_seguridad_y_cache(cliente):
    c, _ = cliente
    r = c.get("/")
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-request-id"]
    assert c.get("/api/estado").headers["cache-control"] == "no-store"
    assert "max-age" in c.get("/static/app.js").headers["cache-control"]


def test_salud_y_compresion(cliente):
    c, _ = cliente
    s = c.get("/api/salud").json()
    assert s["estado"] == "ok" and s["avisos"] > 0
    r = c.get("/api/mercado", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"


def test_enlaces_que_no_son_http_no_salen_de_la_api():
    from app.main import aviso
    assert aviso({"precio": 1, "url_origen": "javascript:alert(1)"})["url_origen"] is None
    assert aviso({"precio": 1, "url_origen": "https://www.fincaraiz.com.co/x/1"})["url_origen"].startswith("https://")


def test_explicacion_en_lote_igual_a_predicciones_separadas(cliente):
    """La estimación agrupa las variantes en una predicción: debe dar lo mismo que una por una."""
    from modelo import datos as D
    from modelo import entrenar as E
    from modelo import estimar as ES
    df = D.cargar(D.DEMO)
    X = E.preparar(df)
    arbol = E.candidatos(len(df))["Árbol de decisión"].fit(X, df.precio)
    ES.un_hilo(arbol)
    art = {"modelo": arbol, "q_log": 0.2, "referencia": E.referencia(X), "dominio": E.dominio(df, X)}
    datos = {"area_m2": 120, "estrato": 6, "localidad": "Bosa", "habitaciones": 4, "banos": 3, "parqueaderos": 2}
    r = ES.estimar(art, datos)
    fila = ES._fila(datos)
    for e in r["efectos"]:
        alt = fila.copy()
        alt.at[0, e["variable"]] = art["referencia"][e["variable"]]
        esperado = arbol.predict(fila)[0] - arbol.predict(alt)[0]
        assert e["efecto"] == pytest.approx(esperado, abs=1)


def test_inferencia_en_un_solo_hilo():
    import pandas as pd

    from modelo import entrenar as E
    from modelo import estimar as ES
    X = pd.DataFrame({c: [1.0, 2.0, 3.0, 4.0] for c in ["area_m2", "habitaciones", "banos", "estrato",
                                                         "parqueaderos", "antiguedad"]})
    X["localidad"] = ["A", "B", "A", "B"]
    m = E.candidatos(40)["Bosque aleatorio"].fit(X, [1e8, 2e8, 3e8, 4e8])
    ES.un_hilo(m)
    assert m.regressor_.named_steps["reg"].n_jobs == 1


def test_zonas_resumen_y_detalle(cliente):
    c, _ = cliente
    z = c.get("/api/zonas").json()
    nombres = {l["nombre"] for l in z["localidades"]}
    assert len(nombres) == 20 and "Sumapaz" in nombres
    con = [l for l in z["localidades"] if l["n"] >= 3]
    assert all(l["p10_precio"] <= l["mediana_precio"] <= l["p90_precio"] for l in con)
    assert sorted(l["puesto"] for l in con) == list(range(1, len(con) + 1))
    d = c.get("/api/zonas/Suba").json()
    assert d["tipico"]["minimo"] <= d["tipico"]["precio"] <= d["tipico"]["maximo"]
    assert all(a["comparacion"] == "debajo" for a in d["debajo"])
    assert c.get("/api/zonas/Narnia").status_code == 404


def test_avisos_frente_a_lo_esperado(cliente):
    c, _ = cliente
    r = c.get("/api/avisos", params={"comparacion": "debajo", "orden": "diferencia_pct", "por_pagina": 20}).json()
    assert r["total"] > 0
    difs = [a["diferencia_pct"] for a in r["avisos"]]
    assert all(d < -0.10 for d in difs) and difs == sorted(difs)


def test_automatizacion_apagada_en_pruebas(cliente):
    c, _ = cliente
    a = c.get("/api/automatizacion").json()
    assert a["situacion"] == "apagado" and "base" in a
    assert c.post("/api/automatizacion/ejecutar").status_code == 409
