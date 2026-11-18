"""Comparables: búsqueda que se amplía paso a paso, perfil real de la zona y nada inventado."""
import pandas as pd

from modelo import comparables as cp


def avisos(filas):
    df = pd.DataFrame(filas, columns=["localidad", "estrato", "habitaciones", "banos", "parqueaderos", "area_m2", "precio"])
    df["antiguedad"] = None
    df["barrio"] = None
    df["precio_m2"] = df["precio"] / df["area_m2"]
    return df


SUBA = [("Suba", 4, 3, 2, 1, 70 + i, (70 + i) * 6_000_000) for i in range(12)]
KENNEDY = [("Kennedy", 3, 3, 2, 0, 55 + i, (55 + i) * 4_000_000) for i in range(8)]
ENGATIVA = [("Engativá", 4, 3, 2, 1, 72 + i, (72 + i) * 5_500_000) for i in range(7)]


def test_busqueda_exacta_con_suficientes():
    r = cp.estimar(avisos(SUBA + KENNEDY), {"area_m2": 75, "estrato": 4, "localidad": "Suba", "habitaciones": 3,
                                            "banos": 2, "parqueaderos": 1})
    assert r["viable"] and r["paso"] == "exacta" and not r["amplio"]
    assert r["usados"] >= 5 and r["origen"]["localidades"] == [{"localidad": "Suba", "n": r["usados"]}]
    assert abs(r["precio"] - 75 * 6_000_000) < 10_000_000
    assert r["minimo"] <= r["precio"] <= r["maximo"]
    assert r["pasos"][0]["encontrados"] >= 5


def test_amplia_a_localidades_vecinas_y_lo_dice():
    # En Suba solo hay 2 de estrato 4; Engativá (vecina) tiene 7: se amplía y se informa.
    r = cp.estimar(avisos(SUBA[:2] + ENGATIVA + KENNEDY), {"area_m2": 75, "estrato": 4, "localidad": "Suba",
                                                          "habitaciones": 3})
    assert r["viable"] and r["amplio"] and r["paso"] == "vecinas"
    assert [p["clave"] for p in r["pasos"]] == ["exacta", "habitaciones", "estrato", "vecinas"]
    assert any("otras localidades" in d and "Engativá" in d for d in r["diferencias"])
    assert r["confiabilidad"] in ("media", "baja")


def test_sin_comparables_no_hay_precio():
    r = cp.estimar(avisos(KENNEDY), {"area_m2": 400, "estrato": 6, "localidad": "Chapinero", "habitaciones": 5})
    assert r["viable"] is False and r["precio"] is None and r["confiabilidad"] == "insuficiente"
    assert "No se da un precio" in r["razones"][0]


def test_pocos_comparables_confiabilidad_baja():
    r = cp.estimar(avisos(SUBA[:3] + KENNEDY), {"area_m2": 71, "estrato": 4, "localidad": "Suba", "habitaciones": 3})
    assert r["viable"] and r["usados"] == 3 and r["confiabilidad"] == "baja"


def test_perfil_cuenta_lo_que_existe_en_la_zona():
    p = cp.perfil(avisos(SUBA + KENNEDY), "Kennedy")
    assert p["n"] == 8 and p["estratos"] == [{"valor": 3, "n": 8}]
    assert p["parqueaderos"] == [{"valor": 0, "n": 8}]
    assert p["comunes"]["habitaciones"] == {"valor": 3, "porcentaje": 1.0}
    assert p["area"]["min"] == 55 and p["tipos"][0]["n"] == 8
    assert cp.perfil(avisos(SUBA), "Bosa")["n"] == 0


def test_vecinas_desde_los_limites_oficiales():
    v = cp.vecinas()
    assert "Engativá" in v["Suba"] and "Usaquén" in v["Suba"] and "Bosa" not in v["Suba"]


def test_un_solo_comparable_no_da_un_rango_falso():
    r = cp.estimar(avisos(SUBA[:1] + KENNEDY), {"area_m2": 70, "estrato": 4, "localidad": "Suba", "habitaciones": 3},
                   factor_minimo=1.25)
    assert r["usados"] == 1 and r["rango_ampliado"]
    assert r["maximo"] / r["minimo"] > 1.5
