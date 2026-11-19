"""Regresión de la limpieza de atípicos, el m² de los vecinos y la mezcla medida con avisos reales."""
import math

import numpy as np
import pandas as pd

from limpieza import construir_dataset as cd
from modelo import precision
from modelo.vecinos import VecinosM2


def _grupo(n=20, m2=6_000_000, loc="Suba", estrato=4):
    rng = np.random.default_rng(1)
    areas = rng.uniform(50, 120, n)
    return pd.DataFrame({"precio": areas * m2 * rng.uniform(0.9, 1.1, n), "area_m2": areas, "localidad": loc,
                         "estrato": estrato, "url_origen": [f"u{loc}{i}" for i in range(n)]})


def test_atipicos_se_marcan_sin_borrarse():
    df = _grupo()
    df.loc[0, "precio"] *= 10          # un cero de más
    df.loc[1, "precio"] /= 12          # arriendo publicado como venta
    marcado, motivos = cd.marcar_atipicos(df)
    assert len(marcado) == len(df), "no se borra nada"
    assert "muy alto" in marcado.at[0, "atipico"] and "muy bajo" in marcado.at[1, "atipico"]
    assert marcado["atipico"].iloc[2:].isna().all()
    assert motivos["m² muy alto"] == 1 and motivos["m² muy bajo"] == 1


def test_grupo_pequeno_se_compara_con_el_estrato_en_bogota():
    df = pd.concat([_grupo(20), _grupo(3, loc="Bosa")], ignore_index=True)
    df.loc[20, "precio"] *= 9
    marcado, _ = cd.marcar_atipicos(df)
    assert "estrato 4 en Bogotá" in marcado.at[20, "atipico"]


def test_vecinos_no_se_cuentan_a_si_mismos():
    # Cuatro puntos: el m² de cada uno sale solo de los otros tres
    X = np.array([[4.60, -74.10, 50], [4.601, -74.10, 50], [4.60, -74.101, 50], [4.70, -74.05, 50]], float)
    y = np.log(np.array([5e6, 6e6, 7e6, 50e6]) * 50)
    v = VecinosM2(k=2)
    tr = v.fit_transform(X, y)
    assert not np.isclose(tr[3, 0], math.log(50e6)), "el punto aislado no puede ver su propio m²"
    assert np.isclose(tr[0, 0], np.median(np.log([6e6, 7e6])))
    nuevo = v.transform(np.array([[4.6005, -74.1005, 60], [np.nan, np.nan, 60]]))
    assert np.isfinite(nuevo[0, 0]) and np.isnan(nuevo[1, 0])


def test_combinar_usa_pesos_y_rango_medidos():
    cal = {"niveles": {"media": {"n": 50, "fiable": True, "peso_comparables": 0.75, "factor_rango": 1.2,
                                 "error_mezcla": 0.1}}}
    r = precision.combinar(cal, "media", 400e6, 500e6, 1.3)
    esperado = math.exp(0.75 * math.log(400e6) + 0.25 * math.log(500e6))
    assert abs(r["precio"] - esperado) < 1e6 and r["calibrado"]
    assert abs(r["maximo"] / r["precio"] - 1.2) < 0.01
    sin = precision.combinar(None, "baja", 400e6, 500e6, 1.3)
    assert not sin["calibrado"] and sin["peso_comparables"] == 0.25 and sin["maximo"] > sin["precio"] * 1.4
