"""Construye un corte versionado del dataset a partir de inmuebles_crudos.

Uso:
  python -m limpieza.construir_dataset --notas "Corte Sprint 2"

El ciclo automático (scraper/ciclo.py) llama a construir() después de cada recolección.
Solo entran los avisos que siguen disponibles; los retirados quedan en la base con su historial.

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
import logging
import pathlib
from collections import Counter

import numpy as np
import pandas as pd
from sqlalchemy import insert, or_, select

from scraper import db

from . import geo
from . import normalizar as nz

log = logging.getLogger("vivienda.limpieza")

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
            "portal", "fecha_consulta", "publicado", "proyecto", "atipico", "url_origen", "crudo_id"]


def admin_valida(admin: int | None, precio: int) -> int | None:
    """0 significa 'no informada'; montos absurdos (errores de digitación) se anulan."""
    if not admin or admin > REGLAS["admin_max"] or admin > REGLAS["admin_max_fraccion_precio"] * precio:
        return None
    return admin


def normalizar_crudos(crudos: pd.DataFrame) -> tuple[pd.DataFrame, Counter]:
    descartes, filas = Counter(), []
    detalle = log.isEnabledFor(logging.DEBUG)

    def descartar(motivo, r, valor=""):
        descartes[motivo] += 1
        if detalle:
            log.debug("Descartado (%s) %s %s", motivo, valor, r.url_origen)

    for r in crudos.itertuples(index=False):
        if not nz.es_apartamento(r.tipo_txt):
            descartar("no es apartamento", r, f"tipo={r.tipo_txt!r}")
            continue
        p, a = nz.precio(r.precio_txt), nz.area(r.area_txt)
        if p is None or a is None:
            descartar("sin precio o sin área", r, f"precio={r.precio_txt!r} área={r.area_txt!r}")
            continue
        if p <= 0:
            descartar("precio igual a cero", r)
            continue
        if not REGLAS["area_min_m2"] <= a <= REGLAS["area_max_m2"]:
            descartar("área fuera de 25–500 m²", r, f"área={a}")
            continue
        if not REGLAS["precio_min"] <= p <= REGLAS["precio_max"]:
            descartar("precio fuera de 80–3.000 millones", r, f"precio={p}")
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
        elif detalle:
            log.debug("Sin localidad: barrio=%r localidad_txt=%r coordenadas=(%s, %s) %s", barrio, r.localidad_txt,
                      lat, lon, r.url_origen)
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
            "publicado": nz.fecha(getattr(r, "publicado_txt", None)),
            # Proyectos sobre planos o para estrenar se venden distinto que los usados: se marcan.
            "proyecto": bool("proyecto" in nz.simple(r.tipo_txt) or nz.estado(r.estado_txt) == "nuevo"),
            "atipico": None,
            "url_origen": r.url_origen, "crudo_id": r.id,
        })
    return pd.DataFrame(filas, columns=COLUMNAS), descartes


Z_ATIPICO = 3.5       # desviaciones robustas (MAD) del log del m² en su grupo
MINIMO_GRUPO = 8


def marcar_atipicos(df: pd.DataFrame) -> tuple[pd.DataFrame, Counter]:
    """Marca (sin borrar) los avisos cuyo precio por m² está muy lejos de los de su misma localidad y
    estrato: precios con ceros de más o de menos, arriendos publicados como venta, áreas mal
    digitadas. Se usa la mediana y la desviación absoluta mediana del log del m², que no se dejan
    arrastrar por los mismos atípicos. Con menos de 8 avisos en el grupo se compara con el estrato
    en toda Bogotá. Los marcados quedan en el dataset y en la lista de avisos (con su motivo), pero
    no entran al modelo, a los comparables ni a las estadísticas de zona."""
    df = df.copy()
    motivos = Counter()
    if df.empty:
        return df, motivos
    lm2 = np.log(df["precio"].astype(float) / df["area_m2"].astype(float))
    marca = pd.Series([None] * len(df), index=df.index, dtype=object)

    def revisar(grupo_idx, nombre):
        v = lm2.loc[grupo_idx]
        med = v.median()
        mad = 1.4826 * (v - med).abs().median()
        if not mad or np.isnan(mad):
            return
        z = (v - med) / max(mad, 0.05)
        for i in z.index[z.abs() > Z_ATIPICO]:
            if marca.at[i] is None:
                lado = "alto" if z.at[i] > 0 else "bajo"
                marca.at[i] = f"m² muy {lado} para {nombre} (×{np.exp(v.at[i] - med):.1f} la mediana)"
    hechos = set()
    for (loc, est), g in df.groupby(["localidad", "estrato"], dropna=True):
        if len(g) >= MINIMO_GRUPO:
            revisar(g.index, f"estrato {int(est)} en {loc}")
            hechos.update(g.index)
    for est, g in df.groupby("estrato", dropna=True):
        resto = g.index.difference(list(hechos))
        if len(g) >= MINIMO_GRUPO and len(resto):
            revisar(g.index, f"estrato {int(est)} en Bogotá")
    df["atipico"] = marca
    for m in marca.dropna():
        motivos["m² muy alto" if "muy alto" in m else "m² muy bajo"] += 1
    if motivos:
        log.info("Avisos atípicos marcados (no se borran): %s", dict(motivos))
        for i in marca.dropna().index[:50]:
            log.debug("Atípico: %s — %s", df.at[i, "url_origen"], marca.at[i])
    return df, motivos


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
                    log.debug("Duplicado: %s se parece a %s", df.at[j, "url_origen"], df.at[i, "url_origen"])
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


def precios_anteriores(c) -> pd.DataFrame:
    """Último cambio de precio de cada aviso: con él la interfaz muestra si subió o bajó."""
    t = db.cambios
    filas = pd.read_sql(select(t.c.url_origen, t.c.fecha, t.c.antes).where(t.c.campo == "precio")
                        .order_by(t.c.fecha), c)
    if filas.empty:
        return pd.DataFrame(columns=["url_origen", "precio_anterior", "fecha_cambio_precio"])
    filas = filas.groupby("url_origen").tail(1)
    return pd.DataFrame({"url_origen": filas["url_origen"],
                         "precio_anterior": filas["antes"].map(nz.precio),
                         "fecha_cambio_precio": pd.to_datetime(filas["fecha"]).dt.date})


def limpiar_versiones_viejas(carpeta: pathlib.Path, conservar: int):
    cortes = sorted(carpeta.glob("dataset_v*.csv"), key=lambda r: int(r.stem.split("_v")[1]))
    for r in cortes[:-conservar]:
        log.info("Borrando versión vieja del dataset: %s (se conservan %d)", r.name, conservar)
        r.unlink(missing_ok=True)
        r.with_name(r.name.replace("dataset_v", "reporte_calidad_v").replace(".csv", ".md")).unlink(missing_ok=True)


def construir(notas: str = "", solo_si_cambia: bool = False, eng=None, conservar: int | None = None):
    """Construye el corte. Devuelve (versión, ruta del CSV, registros), o None si no hubo cambios."""
    eng = eng or db.motor()
    t = db.inmuebles_crudos
    with eng.begin() as c:
        crudos = pd.read_sql(select(t).where(or_(t.c.disponible.is_(None), t.c.disponible.is_(True))), c)
        anterior = c.execute(select(db.dataset_versiones.c.version, db.dataset_versiones.c.checksum_sha256)
                             .order_by(db.dataset_versiones.c.version.desc()).limit(1)).first()
        version = (anterior.version if anterior else 0) + 1
        previos = precios_anteriores(c)
    log.info("Construyendo dataset: %d avisos crudos disponibles, última versión %s",
             len(crudos), anterior.version if anterior else "ninguna")
    if crudos.empty:
        raise SystemExit("No hay registros disponibles en inmuebles_crudos.")
    df, descartes = normalizar_crudos(crudos)
    log.info("Normalización: %d válidos de %d. Motivos: %s", len(df), len(crudos), dict(descartes))
    df, n_dup = deduplicar(df)
    log.info("Duplicados quitados: %d; quedan %d", n_dup, len(df))
    df, atipicos = marcar_atipicos(df)
    for k, v in atipicos.items():
        descartes[f"(marcado, no borrado) {k}"] = v
    log.info("Proyectos nuevos o sobre planos: %d", int(df["proyecto"].sum()) if len(df) else 0)
    vacios = {c: int(n) for c, n in df.isna().sum().items() if n}
    log.info("Campos vacíos en el dataset limpio: %s", vacios)
    suma = checksum(df)
    salida = RAIZ / "datos"
    if solo_si_cambia and anterior and anterior.checksum_sha256 == suma \
            and (salida / f"dataset_v{anterior.version}.csv").exists():
        log.info("Mismo contenido que la versión %s (checksum igual): no se crea otra", anterior.version)
        return None
    with eng.begin() as c:
        c.execute(insert(db.dataset_versiones), [{
            "version": version, "n_crudos": len(crudos), "n_registros": len(df),
            "checksum_sha256": suma, "notas": notas,
            "reglas": json.dumps({"reglas": REGLAS, "descartes": dict(descartes),
                                  "duplicados": n_dup}, ensure_ascii=False)}])
        if len(df):
            filas = df.assign(version_dataset=version,
                              fecha_consulta=pd.to_datetime(df["fecha_consulta"]).dt.date)
            filas = filas.astype(object).where(filas.notna(), None)
            c.execute(insert(db.inmuebles_limpios), filas.to_dict("records"))
    salida.mkdir(exist_ok=True)
    ruta = salida / f"dataset_v{version}.csv"
    df.drop(columns=["crudo_id"]).merge(previos, on="url_origen", how="left") \
        .to_csv(ruta, index=False, encoding="utf-8-sig")
    (salida / f"reporte_calidad_v{version}.md").write_text(
        reporte(df, len(crudos), descartes, n_dup, version, suma), encoding="utf-8")
    if conservar:
        limpiar_versiones_viejas(salida, conservar)
    log.info("Dataset v%s: %d registros limpios de %d crudos (%d duplicados). Guardado en %s",
             version, len(df), len(crudos), n_dup, ruta)
    return version, ruta, len(df)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--notas", default="")
    args = ap.parse_args(argv)
    from comun import registro
    registro.configurar("limpieza")
    construir(args.notas)


if __name__ == "__main__":
    main()
