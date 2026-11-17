"""Estimación de un apartamento con el modelo guardado: precio, rango y explicación."""
import math

import joblib
import numpy as np
import pandas as pd

from . import datos as D
from .entrenar import MODELO, preparar

NOMBRES = {"area_m2": "Área", "habitaciones": "Habitaciones", "banos": "Baños", "estrato": "Estrato",
           "parqueaderos": "Parqueaderos", "antiguedad": "Antigüedad", "localidad": "Localidad"}


def cargar_modelo() -> dict | None:
    """Carga el modelo guardado. joblib usa pickle: solo se cargan artefactos producidos por este
    mismo proyecto (artefactos/ no se sube al repositorio ni se acepta desde fuera)."""
    if not MODELO.exists():
        return None
    art = joblib.load(MODELO)
    un_hilo(art["modelo"])
    return art


def un_hilo(modelo) -> None:
    """En inferencia se predicen pocas filas: repartirlas en varios hilos cuesta más que calcularlas.
    Con un bosque de 300 árboles, n_jobs=1 baja una predicción de unos 58 ms a 13 ms."""
    for est in _componentes(modelo):
        if "n_jobs" in getattr(est, "get_params", lambda: {})():
            est.set_params(n_jobs=1)


def _componentes(m):
    yield m
    for hijo in (getattr(m, "regressor_", None), getattr(m, "regressor", None)):
        if hijo is not None:
            yield from _componentes(hijo)
    for _, paso in getattr(m, "steps", []):
        yield from _componentes(paso)


def _fila(datos: dict) -> pd.DataFrame:
    return preparar(pd.DataFrame([{c: datos.get(c) for c in D.VARIABLES}]))


def _valor_legible(var: str, v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "sin dato"
    if var == "area_m2":
        return f"{v:.0f} m²"
    if var == "antiguedad":
        return f"{v:.0f} años"
    if var == "localidad":
        return str(v)
    return f"{v:.0f}"


def estimar(art: dict, datos: dict) -> dict:
    modelo, q, ref = art["modelo"], art["q_log"], art["referencia"]
    X = _fila(datos)

    # Explicación: cuánto cambia la estimación si una sola característica toma el valor del
    # apartamento típico del dataset. No es una descomposición exacta (los efectos se combinan),
    # pero responde la pregunta práctica: "¿qué está subiendo o bajando este precio?".
    # La fila original y sus variantes van en UNA sola predicción (antes eran hasta 8 llamadas).
    variantes = [var for var in D.VARIABLES
                 if var in ref and datos.get(var) is not None and X.at[0, var] != ref[var]]
    lote = pd.concat([X] * (1 + len(variantes)), ignore_index=True)
    for i, var in enumerate(variantes, start=1):
        lote.at[i, var] = ref[var]
    pred = modelo.predict(lote)
    precio = float(pred[0])
    efectos = []
    for i, var in enumerate(variantes, start=1):
        efecto = precio - float(pred[i])
        if abs(efecto) >= 0.005 * precio:
            efectos.append({"variable": var, "nombre": NOMBRES[var], "efecto": round(efecto),
                            "valor": _valor_legible(var, X.at[0, var]), "tipico": _valor_legible(var, ref[var])})
    efectos.sort(key=lambda e: -abs(e["efecto"]))

    return {
        "precio": round(precio, -6),
        "minimo": round(precio * math.exp(-q), -6),
        "maximo": round(precio * math.exp(q), -6),
        "precio_m2": round(precio / float(datos["area_m2"]), -3),
        "efectos": efectos,
        "referencia": {k: _valor_legible(k, v) for k, v in ref.items()},
        "advertencias": advertencias(art["dominio"], datos),
    }


def advertencias(dom: dict, datos: dict) -> list[str]:
    avisos = []
    for var, r in dom["numericas"].items():
        v = datos.get(var)
        if v is None:
            continue
        if v < r["min"] or v > r["max"]:
            avisos.append(f"{NOMBRES[var]} de {_valor_legible(var, float(v))} está fuera de lo que el modelo "
                          f"ha visto ({_valor_legible(var, r['min'])} a {_valor_legible(var, r['max'])}).")
    loc = datos.get("localidad")
    if loc and dom["localidades"].get(loc, 0) < 5:
        n = dom["localidades"].get(loc, 0)
        avisos.append(f"Hay {n} aviso{'s' if n != 1 else ''} de {loc} en los datos: "
                      "la estimación para esa zona es poco confiable.")
    est = datos.get("estrato")
    if est is not None and dom["estratos"].get(str(int(est)), 0) < 5:
        avisos.append(f"Hay muy pocos avisos de estrato {int(est)} en los datos.")
    return avisos


def similares(df: pd.DataFrame, datos: dict, k: int = 6) -> pd.DataFrame:
    """Avisos parecidos: mismo estrato si existe y área cercana; prioriza la misma localidad."""
    d = df.copy()
    area = float(datos["area_m2"])
    d["_dist"] = (np.abs(np.log(d["area_m2"] / area)) * 4
                  + (d["estrato"] - (datos.get("estrato") or d["estrato"].median())).abs().fillna(2)
                  + (d["localidad"] != datos.get("localidad")).astype(float) * 0.8
                  + (d["habitaciones"] - (datos.get("habitaciones") or 0)).abs().fillna(1) * 0.3)
    return d.nsmallest(k, "_dist")
