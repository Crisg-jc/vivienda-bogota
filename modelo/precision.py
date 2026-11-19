"""¿Cuánto aciertan de verdad los comparables y el modelo? Prueba con avisos reales.

Para una muestra de avisos (hasta 400), se hace como si cada uno fuera un apartamento nuevo:
se buscan sus comparables SIN él (dejar uno fuera) y se toma la predicción del modelo hecha sin
haberlo visto (fuera de pliegue). Con eso, para cada nivel de confiabilidad se mide:
  - el error mediano de los comparables, del modelo y de su mezcla;
  - qué peso darle a cada uno para fallar menos (se elige entre 0, 25, 50, 75 y 100 %);
  - el ancho de rango que contiene el precio real 8 de cada 10 veces;
  - y si "confiabilidad alta" de verdad falla menos que "baja".
La app usa estos pesos y rangos en cada estimación y muestra estas cifras en la sección Modelo.
"""
from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

from . import comparables

log = logging.getLogger("vivienda.precision")
PESOS = (0.0, 0.25, 0.5, 0.75, 1.0)
MINIMO_PRUEBAS = 15
PESO_POR_DEFECTO = {"alta": 0.75, "media": 0.5, "baja": 0.25}
NIVEL_RANGO = 0.80
CAMPOS = ("area_m2", "estrato", "localidad", "habitaciones", "banos", "parqueaderos", "antiguedad")


def _dato(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) or pd.isna(v) else (
        v if isinstance(v, str) else float(v))


def evaluar(df: pd.DataFrame, oof, q_modelo: float, muestra: int = 400, semilla: int = 42) -> dict:
    oof = pd.Series(np.asarray(oof, dtype=float), index=df.index)
    elegidos = df.sample(min(muestra, len(df)), random_state=semilla).index
    filas = []
    for i in elegidos:
        r = df.loc[i]
        datos = {c: _dato(r.get(c)) for c in CAMPOS}
        if datos["estrato"] is None:
            continue
        datos["estrato"] = int(datos["estrato"])
        c = comparables.estimar(df.drop(index=i), datos, factor_minimo=math.exp(q_modelo))
        filas.append({"nivel": c["confiabilidad"], "real": float(r["precio"]), "modelo": float(oof.at[i]),
                      "comparables": c["precio"], "c_min": c["minimo"], "c_max": c["maximo"]})
    t = pd.DataFrame(filas)
    niveles = {}
    for nivel in ("alta", "media", "baja", "insuficiente"):
        g = t[t["nivel"] == nivel] if len(t) else t
        if not len(g):
            continue
        res = {"n": int(len(g))}
        lr, lm = np.log(g["real"]), np.log(g["modelo"])
        res["error_modelo"] = round(float(np.median(np.abs(g["modelo"] / g["real"] - 1))), 4)
        if nivel != "insuficiente":
            lc = np.log(g["comparables"].astype(float))
            res["error_comparables"] = round(float(np.median(np.abs(g["comparables"] / g["real"] - 1))), 4)
            res["cobertura_rango_comparables"] = round(float(((g["real"] >= g["c_min"]) & (g["real"] <= g["c_max"])).mean()), 3)
            errores = {w: float(np.median(np.abs(w * lc + (1 - w) * lm - lr))) for w in PESOS}
            fiable = len(g) >= MINIMO_PRUEBAS
            w = min(errores, key=errores.get) if fiable else PESO_POR_DEFECTO[nivel]
            mezcla = w * lc + (1 - w) * lm
            residuos = np.sort(np.abs(mezcla - lr))
            k = min(len(residuos) - 1, math.ceil((len(residuos) + 1) * NIVEL_RANGO) - 1)
            res.update(peso_comparables=w, fiable=fiable,
                       error_mezcla=round(float(np.median(np.abs(np.exp(mezcla - lr) - 1))), 4),
                       factor_rango=round(float(math.exp(max(residuos[k], q_modelo * 0.5))), 4) if fiable else None)
        niveles[nivel] = res
    total = int(len(t))
    log.info("Precisión medida con %d avisos dejados fuera: %s", total, niveles)
    return {"muestra": total, "niveles": niveles, "nivel_rango": NIVEL_RANGO,
            "metodo": "Cada aviso de la muestra se estimó como si fuera nuevo: comparables buscados sin él y "
                      "predicción del modelo hecha sin haberlo visto. Error = mediana de |estimado / real − 1|."}


def combinar(precision: dict | None, nivel: str, precio_comparables: float, precio_modelo: float,
             factor_modelo: float) -> dict:
    """Precio final y rango para una estimación, con los pesos y anchos medidos por evaluar()."""
    cal = ((precision or {}).get("niveles") or {}).get(nivel) or {}
    fiable = bool(cal.get("fiable"))
    w = cal.get("peso_comparables") if fiable else PESO_POR_DEFECTO.get(nivel, 0.5)
    precio = math.exp(w * math.log(precio_comparables) + (1 - w) * math.log(precio_modelo))
    factor = cal.get("factor_rango") if fiable and cal.get("factor_rango") else factor_modelo * (1.15 if nivel == "baja" else 1.0)
    return {"precio": round(precio, -6), "minimo": round(precio / factor, -6), "maximo": round(precio * factor, -6),
            "peso_comparables": w, "calibrado": fiable, "factor": factor,
            "error_tipico": cal.get("error_mezcla"), "pruebas": cal.get("n", 0)}
