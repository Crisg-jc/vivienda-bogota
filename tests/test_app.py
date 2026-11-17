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
    assert r.status_code == 200 and "Estimar precio" in r.text
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
