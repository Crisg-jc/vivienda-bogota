"""Ubicación y carga del dataset que usan el modelo y la aplicación.

Orden de búsqueda:
  1. la ruta de la variable de entorno VIVIENDA_DATASET, si existe;
  2. el corte más reciente datos/dataset_vN.csv producido por limpieza.construir_dataset;
  3. datos/demo/dataset_demo.csv, datos SINTÉTICOS, SOLO si VIVIENDA_DEMO=1 (pruebas y desarrollo).
     Sin esa variable, la aplicación nunca los usa: sin datos reales, dice que no los hay.

El tercer caso existe solo para que la aplicación se pueda probar sin recolectar nada. Todo lo que
venga de él se marca como demostración en la API y en la interfaz.
"""
import hashlib
import logging
import os
import pathlib
import re

import pandas as pd

RAIZ = pathlib.Path(__file__).resolve().parents[1]
DEMO = RAIZ / "datos" / "demo" / "dataset_demo.csv"
log = logging.getLogger("vivienda.datos")

NUMERICAS = ["area_m2", "habitaciones", "banos", "estrato", "parqueaderos", "antiguedad", "piso",
             "administracion", "proyecto"]
COORDENADAS = ["latitud", "longitud"]      # entran al modelo por el m² de los vecinos (modelo/vecinos.py)
CATEGORICAS = ["localidad"]
VARIABLES = NUMERICAS + COORDENADAS + CATEGORICAS
# Las que se explican en "qué mueve el precio": las coordenadas se explican a través de la localidad
EXPLICABLES = [v for v in NUMERICAS if v != "proyecto"] + CATEGORICAS
OBJETIVO = "precio"


def ubicar() -> tuple[pathlib.Path | None, str]:
    """Devuelve (ruta, tipo), donde tipo es 'real' o 'demo'."""
    propia = os.environ.get("VIVIENDA_DATASET")
    if propia:
        ruta = pathlib.Path(propia)
        if not ruta.exists():
            log.error("VIVIENDA_DATASET apunta a %s, que no existe", ruta)
            raise SystemExit(f"VIVIENDA_DATASET apunta a {ruta}, que no existe.")
        log.debug("Dataset fijado por VIVIENDA_DATASET: %s", ruta)
        return ruta, "demo" if ruta.resolve() == DEMO.resolve() else "real"
    cortes = []
    for r in (RAIZ / "datos").glob("dataset_v*.csv"):
        m = re.fullmatch(r"dataset_v(\d+)\.csv", r.name)
        if m:
            cortes.append((int(m.group(1)), r))
    if cortes:
        log.debug("Cortes encontrados en datos/: %s", sorted(v for v, _ in cortes))
        return max(cortes)[1], "real"
    if DEMO.exists() and os.environ.get("VIVIENDA_DEMO") == "1":
        log.debug("No hay cortes reales en datos/ y VIVIENDA_DEMO=1: se usa el de demostración")
        return DEMO, "demo"
    log.debug("No hay ningún dataset en %s", RAIZ / "datos")
    return None, "ninguno"


def huella(ruta: pathlib.Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def cargar(ruta: pathlib.Path) -> pd.DataFrame:
    try:
        df = pd.read_csv(ruta, encoding="utf-8-sig")
    except Exception:
        log.exception("No se pudo leer el CSV %s (¿archivo dañado o abierto en Excel?)", ruta)
        raise
    log.debug("CSV %s leído: %d filas, columnas %s", ruta.name, len(df), list(df.columns))
    faltan = [c for c in [OBJETIVO, "area_m2", "estrato"] if c not in df.columns]
    if faltan:
        log.error("%s no tiene las columnas obligatorias %s", ruta.name, faltan)
        raise SystemExit(f"{ruta.name} no tiene las columnas {faltan}.")
    for c in [x for x in NUMERICAS if x != "proyecto"] + [OBJETIVO, "latitud", "longitud"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        else:
            df[c] = float("nan")
    for c in ["localidad", "barrio", "url_origen", "portal", "atipico"]:
        if c not in df.columns:
            df[c] = None
        # Una columna de texto toda vacía llega como float (NaN): se fuerza a texto para que los cruces
        # por enlace y los filtros por barrio no fallen (regresión: 500 en /api/tendencia).
        df[c] = df[c].astype(object).where(df[c].notna(), None)
    df["atipico"] = df["atipico"].where(df["atipico"].notna() & (df["atipico"].astype(str) != ""), None)
    if "proyecto" in df.columns and df["proyecto"].notna().any():
        df["proyecto"] = df["proyecto"].map(lambda v: 1.0 if str(v).strip().lower() in ("true", "1", "1.0") else 0.0)
    else:      # cortes anteriores sin la marca: 0 años de antigüedad = para estrenar
        df["proyecto"] = (df["antiguedad"] == 0).astype(float)
    # Mismas reglas mínimas de limpieza/construir_dataset: un registro sin precio o área no sirve.
    antes = len(df)
    df = df[df[OBJETIVO].gt(0) & df["area_m2"].gt(0)].copy()
    if len(df) < antes:
        log.info("%s: %d filas sin precio o sin área se dejan fuera", ruta.name, antes - len(df))
    df["precio_m2"] = df[OBJETIVO] / df["area_m2"]
    return df.reset_index(drop=True)


def utiles(df: pd.DataFrame) -> pd.DataFrame:
    """Avisos que sirven para aprender y comparar: sin los marcados como atípicos en la limpieza."""
    return df[df["atipico"].isna()] if "atipico" in df.columns else df
