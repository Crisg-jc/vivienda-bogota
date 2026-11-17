"""Construye un corte versionado del dataset a partir de inmuebles_crudos.

Uso:
  python -m limpieza.construir_dataset --notas "Corte Sprint 2"

Produce:
  - filas en inmuebles_limpios con version_dataset = N
  - una fila en dataset_versiones con conteos, reglas y suma de verificación SHA-256
  - datos/dataset_vN.csv            (el archivo que usa el cuaderno de modelado)
  - datos/reporte_calidad_vN.md     (evidencia para el informe: descartes, completitud, cobertura)

El filtro de precio por m² en percentiles 1 y 99 NO se aplica aquí: debe calcularse solo con el
conjunto de entrenamiento, después de separar la prueba, para no filtrar información de la prueba.
"""
import argparse
import hashlib
import json
import pathlib
from collections import Counter

import pandas as pd
from sqlalchemy import func, insert, select

from scraper import db
from . import geo
from . import normalizar as nz

RAIZ = pathlib.Path(__file__).resolve().parents[1]

REGLAS = {
    "area_min_m2": 25, "area_max_m2": 500,                 # documento, sección 9.3
    "precio_min": 80_000_000, "precio_max": 3_000_000_000,  # presentación, diapositiva 10
    "habitaciones_max": 6, "banos_max": 6, "parqueaderos_max": 4,
    "admin_max": 5_000_000, "admin_max_fraccion_precio": 0.01,
    "dup_tolerancia_area_m2": 1.0, "dup_tolerancia_precio": 0.02,
}

COLUMNAS = ["precio", "area_m2", "habitaciones", "banos", "estrato", "parqueaderos",
            "localidad", "barrio", "antiguedad", "administracion",
            "piso", "estado", "latitud", "longitud",
            "portal", "fecha_consulta", "url_origen", "crudo_id"]


def admin_valida(admin: int | None, precio: int) -> int | None:
    """0 significa 'no informada'; montos absurdos (errores de digitación) se anulan."""
    if not admin or admin > REGLAS["admin_max"] or admin > REGLAS["admin_max_fraccion_precio"] * precio:
        return None
    return admin


def normalizar_crudos(crudos: pd.DataFrame) -> tuple[pd.DataFrame, Counter]:
    descartes, filas = Counter(), []
    for r in crudos.itertuples(index=False):
        if not nz.es_apartamento(r.tipo_txt):
            descartes["no es apartamento"] += 1
            continue
        p, a = nz.precio(r.precio_txt), nz.area(r.area_txt)
        if p is None or a is None:
            descartes["sin precio o sin área"] += 1
            continue
        if p <= 0:
            descartes["precio igual a cero"] += 1
            continue
        if not REGLAS["area_min_m2"] <= a <= REGLAS["area_max_m2"]:
            descartes["área fuera de 25–500 m²"] += 1
            continue
        if not REGLAS["precio_min"] <= p <= REGLAS["precio_max"]:
            descartes["precio fuera de 80–3.000 millones"] += 1
            continue

        def rango(v, lo, hi):
            return v if v is not None and lo <= v <= hi else None

        barrio = nz.simple(r.barrio_txt).title() or None
        lat = nz.coordenada(r.latitud_txt, 4.45, 4.85)
        lon = nz.coordenada(r.longitud_txt, -74.25, -73.98)
        loc = nz.localidad(r.localidad_txt, barrio)
        origen_loc = "aviso" if loc else None
        if not loc:
            loc = geo.localidad_por_coordenadas(lat, lon)
            origen_loc = "coordenadas" if loc else None
        if not loc:                       # el 'barrio' a veces es el nombre de la localidad
            loc = nz.localidad(r.barrio_txt)
            origen_loc = "nombre del barrio" if loc else None
        if origen_loc:
            descartes[f"(info) localidad tomada de {origen_loc}"] += 1
        filas.append({
            "precio": p, "area_m2": round(a, 2),
            "habitaciones": rango(nz.entero(r.habitaciones_txt), 1, REGLAS["habitaciones_max"]),
            "banos": rango(nz.entero(r.banos_txt), 1, REGLAS["banos_max"]),
            "estrato": rango(nz.entero(r.estrato_txt), 1, 6),
            "parqueaderos": rango(nz.entero(r.parqueaderos_txt), 0, REGLAS["parqueaderos_max"]),
            "localidad": loc,
            "barrio": barrio,
            "antiguedad": rango(nz.antiguedad(r.antiguedad_txt), 0, 80),
            "administracion": admin_valida(nz.precio(r.administracion_txt), p),
            "piso": rango(nz.entero(r.piso_txt), 1, 60),
            "estado": nz.estado(r.estado_txt),
            # Rectángulo aproximado de Bogotá urbana; fuera de él la coordenada se descarta
            "latitud": lat,
            "longitud": lon,
            "portal": r.portal, "fecha_consulta": r.fecha_consulta,
            "url_origen": r.url_origen, "crudo_id": r.id,
        })
    return pd.DataFrame(filas, columns=COLUMNAS), descartes


def deduplicar(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Marca como duplicados avisos casi idénticos: mismas habitaciones, baños y localidad
    (o barrio), área a ±1 m² y precio a ±2 %. Se conserva el primero consultado.

    No se exige igualdad exacta: el mismo apartamento suele publicarse con 1 m² o unos pesos
    de diferencia entre portales. Tampoco se usa una restricción UNIQUE en la base de datos,
    porque eliminaría unidades distintas pero idénticas de un mismo edificio nuevo; por eso
    los posibles duplicados quedan listados en el reporte para revisión manual.
    """
    df = df.sort_values(["fecha_consulta", "crudo_id"]).reset_index(drop=True)
    eliminar = set()
    zona = df["localidad"].fillna(df["barrio"]).fillna("?")
    grupos = df.assign(_z=zona).groupby(["habitaciones", "banos", "_z"], dropna=False).groups
    for idx in grupos.values():
        idx = list(idx)
        for i_pos, i in enumerate(idx):
            if i in eliminar:
                continue
            for j in idx[i_pos + 1:]:
                if j in eliminar:
                    continue
                if (abs(df.at[i, "area_m2"] - df.at[j, "area_m2"]) <= REGLAS["dup_tolerancia_area_m2"]
                        and abs(df.at[i, "precio"] - df.at[j, "precio"])
                        <= REGLAS["dup_tolerancia_precio"] * df.at[i, "precio"]):
                    eliminar.add(j)
    return df.drop(index=list(eliminar)).reset_index(drop=True), len(eliminar)


def checksum(df: pd.DataFrame) -> str:
    contenido = (df.drop(columns=["crudo_id"]).sort_values("url_origen")
                 .to_csv(index=False).encode("utf-8"))
    return hashlib.sha256(contenido).hexdigest()


def reporte(df, n_crudos, descartes, n_dup, version, suma) -> str:
    clave = ["precio", "area_m2", "estrato", "localidad"]
    completos = df[clave].notna().all(axis=1).mean() * 100 if len(df) else 0
    nulos = (df[COLUMNAS[:14]].isna().mean() * 100).round(1)
    lineas = [f"# Reporte de calidad — dataset v{version}", "",
              f"- Registros crudos: {n_crudos}",
              f"- Registros limpios: {len(df)}",
              f"- Duplicados eliminados: {n_dup}",
              f"- Completitud de variables clave (precio, área, estrato, localidad): {completos:.1f} % (meta ≥ 95 %)",
              f"- Suma SHA-256: `{suma}`", "", "## Descartes por regla", "",
              "Las filas que empiezan por (info) no son descartes: indican de dónde salió la localidad.", "",
              "| Regla | Registros |", "| --- | --- |"]
    lineas += [f"| {k} | {v} |" for k, v in descartes.most_common()]
    lineas += ["", "## Porcentaje de valores faltantes por variable", "",
               "| Variable | % faltante |", "| --- | --- |"]
    lineas += [f"| {k} | {v} |" for k, v in nulos.items()]
    lineas += ["", "## Registros por localidad", "", "| Localidad | Registros |", "| --- | --- |"]
    lineas += [f"| {k} | {v} |" for k, v in df["localidad"].fillna("(sin localidad)").value_counts().items()]
    sin_loc = df.loc[df["localidad"].isna() & df["barrio"].notna(), "barrio"].value_counts().head(40)
    if len(sin_loc):
        lineas += ["", "## Barrios sin localidad asignada (agregar a datos/barrios_localidad.csv)", ""]
        lineas += [f"- {b} ({n})" for b, n in sin_loc.items()]
    lineas += ["", "## Registros por portal", "", "| Portal | Registros |", "| --- | --- |"]
    lineas += [f"| {k} | {v} |" for k, v in df["portal"].value_counts().items()]
    if len(df):
        d = df[["precio", "area_m2"]].astype(float).describe()
        lineas += ["", "## Resumen numérico", "", "| Estadístico | Precio (COP) | Área (m²) |", "| --- | --- | --- |"]
        lineas += [f"| {i} | {d.at[i, 'precio']:,.0f} | {d.at[i, 'area_m2']:,.1f} |" for i in d.index]
    return "\n".join(lineas) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--notas", default="")
    args = ap.parse_args(argv)
    eng = db.motor()
    with eng.begin() as c:
        crudos = pd.read_sql(select(db.inmuebles_crudos), c)
        version = (c.scalar(select(func.max(db.dataset_versiones.c.version))) or 0) + 1
    if crudos.empty:
        raise SystemExit("No hay registros en inmuebles_crudos.")
    df, descartes = normalizar_crudos(crudos)
    df, n_dup = deduplicar(df)
    suma = checksum(df)
    with eng.begin() as c:
        c.execute(insert(db.dataset_versiones), [{
            "version": version, "n_crudos": len(crudos), "n_registros": len(df),
            "checksum_sha256": suma, "notas": args.notas,
            "reglas": json.dumps({"reglas": REGLAS, "descartes": dict(descartes),
                                  "duplicados": n_dup}, ensure_ascii=False)}])
        if len(df):
            filas = df.assign(version_dataset=version,
                              fecha_consulta=pd.to_datetime(df["fecha_consulta"]).dt.date)
            filas = filas.astype(object).where(filas.notna(), None)
            c.execute(insert(db.inmuebles_limpios), filas.to_dict("records"))
    salida = RAIZ / "datos"
    salida.mkdir(exist_ok=True)
    df.drop(columns=["crudo_id"]).to_csv(salida / f"dataset_v{version}.csv", index=False, encoding="utf-8-sig")
    (salida / f"reporte_calidad_v{version}.md").write_text(
        reporte(df, len(crudos), descartes, n_dup, version, suma), encoding="utf-8")
    print(f"Dataset v{version}: {len(df)} registros limpios de {len(crudos)} crudos "
          f"({n_dup} duplicados). Archivos en datos/.")


if __name__ == "__main__":
    main()
