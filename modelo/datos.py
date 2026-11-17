"""Ubicación y carga del dataset que usan el modelo y la aplicación.

Orden de búsqueda:
  1. la ruta de la variable de entorno VIVIENDA_DATASET, si existe;
  2. el corte más reciente datos/dataset_vN.csv producido por limpieza.construir_dataset;
  3. datos/demo/dataset_demo.csv, datos SINTÉTICOS creados con `python -m modelo.demo`.

El tercer caso existe solo para que la aplicación se pueda probar sin recolectar nada. Todo lo que
venga de él se marca como demostración en la API y en la interfaz.
"""
import hashlib
import os
import pathlib
import re

import pandas as pd

RAIZ = pathlib.Path(__file__).resolve().parents[1]
DEMO = RAIZ / "datos" / "demo" / "dataset_demo.csv"

NUMERICAS = ["area_m2", "habitaciones", "banos", "estrato", "parqueaderos", "antiguedad"]
CATEGORICAS = ["localidad"]
VARIABLES = NUMERICAS + CATEGORICAS
OBJETIVO = "precio"


def ubicar() -> tuple[pathlib.Path | None, str]:
    """Devuelve (ruta, tipo), donde tipo es 'real' o 'demo'."""
    propia = os.environ.get("VIVIENDA_DATASET")
    if propia:
        ruta = pathlib.Path(propia)
        if not ruta.exists():
            raise SystemExit(f"VIVIENDA_DATASET apunta a {ruta}, que no existe.")
        return ruta, "demo" if ruta.resolve() == DEMO.resolve() else "real"
    cortes = []
    for r in (RAIZ / "datos").glob("dataset_v*.csv"):
        m = re.fullmatch(r"dataset_v(\d+)\.csv", r.name)
        if m:
            cortes.append((int(m.group(1)), r))
    if cortes:
        return max(cortes)[1], "real"
    if DEMO.exists():
        return DEMO, "demo"
    return None, "ninguno"


def huella(ruta: pathlib.Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def cargar(ruta: pathlib.Path) -> pd.DataFrame:
    df = pd.read_csv(ruta, encoding="utf-8-sig")
    faltan = [c for c in [OBJETIVO, "area_m2", "estrato"] if c not in df.columns]
    if faltan:
        raise SystemExit(f"{ruta.name} no tiene las columnas {faltan}.")
    for c in NUMERICAS + [OBJETIVO, "latitud", "longitud", "administracion"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        else:
            df[c] = pd.NA
    for c in ["localidad", "barrio", "url_origen", "portal"]:
        if c not in df.columns:
            df[c] = None
    # Mismas reglas mínimas de limpieza/construir_dataset: un registro sin precio o área no sirve.
    df = df[df[OBJETIVO].gt(0) & df["area_m2"].gt(0)].copy()
    df["precio_m2"] = df[OBJETIVO] / df["area_m2"]
    return df.reset_index(drop=True)
