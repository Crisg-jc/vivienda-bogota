"""Regresión 'dorada' de la búsqueda de comparables: con datos fijos (semilla 11), las mismas
consultas deben dar los mismos resultados. Si un cambio los mueve a propósito, se regeneran con
    ACTUALIZAR_GOLDEN=1 pytest tests/test_golden.py
y el diff de tests/golden/comparables.json muestra exactamente qué cambió."""
import json
import os
import pathlib

import pytest

from modelo import comparables, demo
from modelo import datos as D

ARCHIVO = pathlib.Path(__file__).parent / "golden" / "comparables.json"
CONSULTAS = [
    {"area_m2": 70, "estrato": 4, "localidad": "Suba", "habitaciones": 3, "banos": 2, "parqueaderos": 1},
    {"area_m2": 45, "estrato": 2, "localidad": "Kennedy", "habitaciones": 2, "banos": 1, "parqueaderos": 0},
    {"area_m2": 140, "estrato": 6, "localidad": "Chapinero", "habitaciones": 3, "banos": 3, "parqueaderos": 2},
    {"area_m2": 60, "estrato": 3, "localidad": None, "habitaciones": 2},
    {"area_m2": 950, "estrato": 1, "localidad": "Usme", "habitaciones": 6},
]
CAMPOS = ("viable", "confiabilidad", "precio", "minimo", "maximo", "encontrados", "usados", "paso")


@pytest.fixture(scope="module")
def df(tmp_path_factory):
    ruta = tmp_path_factory.mktemp("golden") / "d.csv"
    demo.generar(900, 11).to_csv(ruta, index=False)
    return D.utiles(D.cargar(ruta))


def test_comparables_estables(df):
    actuales = []
    for q in CONSULTAS:
        r = comparables.estimar(df, dict(q), factor_minimo=1.25)
        actuales.append({"consulta": q, **{k: r.get(k) for k in CAMPOS},
                         "pasos": [(p["clave"], p["encontrados"]) for p in r["pasos"]]})
    actuales = json.loads(json.dumps(actuales))
    if os.environ.get("ACTUALIZAR_GOLDEN") == "1" or not ARCHIVO.exists():
        ARCHIVO.write_text(json.dumps(actuales, ensure_ascii=False, indent=1), encoding="utf-8")
        pytest.skip("Golden regenerado: revise el diff y súbalo")
    esperados = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    for a, e in zip(actuales, esperados, strict=True):
        for k in ("viable", "confiabilidad", "encontrados", "usados", "paso", "pasos"):
            assert a[k] == e[k], (a["consulta"], k, a[k], e[k])
        for k in ("precio", "minimo", "maximo"):
            if e[k] is None:
                assert a[k] is None
            else:
                assert abs(a[k] / e[k] - 1) < 0.005, (a["consulta"], k, a[k], e[k])
