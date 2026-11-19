"""Estimación por apartamentos comparables, con el perfil real de cada localidad.

Antes de dar un precio se mira qué hay de verdad en la zona:
  - perfil(): qué estratos, habitaciones, baños, parqueaderos, áreas, antigüedades y precios
    tienen los avisos reales de una localidad (o de Bogotá), y cuántos hay de cada cosa;
  - buscar(): los apartamentos más parecidos al consultado. Empieza por la búsqueda más estricta
    (misma localidad, mismo estrato, mismas habitaciones, área parecida) y solo si no alcanza la
    va ampliando paso a paso: ±1 habitación, estrato vecino, localidades vecinas, toda Bogotá.
    Cada paso queda anotado, con cuántos encontró, para mostrarle al usuario qué se hizo;
  - estimar(): precio = mediana del m² de los comparables usados × área consultada, rango
    = cuartiles de ese m² × área, y una confiabilidad con sus razones. Sin comparables no hay
    precio: se dice que no hay información suficiente.

Las localidades vecinas se calculan de los límites oficiales (IDECA) incluidos en el proyecto.
"""
from __future__ import annotations

import json
import logging
import pathlib
from functools import lru_cache

import numpy as np
import pandas as pd

log = logging.getLogger("vivienda.comparables")
GEO = pathlib.Path(__file__).resolve().parents[1] / "app" / "static" / "geo" / "localidades.geojson"

MINIMO = 5          # menos de esto en un paso: se amplía la búsqueda
IDEAL = 10          # con esto o más en una búsqueda estricta, la confiabilidad puede ser alta
MAX_USADOS = 15     # se usan los más parecidos, no todos los que cumplan

ETIQUETAS = {"estrato": "estrato", "habitaciones": "habitaciones", "banos": "baños", "parqueaderos": "parqueaderos"}


# ---------------------------------------------------------------- localidades vecinas
@lru_cache(maxsize=1)
def vecinas() -> dict[str, list[str]]:
    """Dos localidades son vecinas si sus límites quedan a menos de 200 m (los límites incluidos
    están simplificados, por eso no se exige que se toquen exactamente)."""
    try:
        g = json.loads(GEO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.exception("No se pudieron leer los límites de las localidades: no se ampliará a vecinas")
        return {}
    puntos = {}
    for f in g["features"]:
        geo = f["geometry"]
        poligonos = geo["coordinates"] if geo["type"] == "MultiPolygon" else [geo["coordinates"]]
        puntos[f["properties"]["nombre"]] = np.array([c for p in poligonos for anillo in p for c in anillo])
    nombres, salida = list(puntos), {n: [] for n in puntos}
    for i, a in enumerate(nombres):
        for b in nombres[i + 1:]:
            d = np.sqrt(((puntos[a][:, None, :] - puntos[b][None, :, :]) ** 2).sum(-1)).min() * 111_000
            if d < 200:
                salida[a].append(b)
                salida[b].append(a)
    log.debug("Localidades vecinas: %s", salida)
    return salida


# ---------------------------------------------------------------- perfil de la zona
def _conteo(s: pd.Series) -> list[dict]:
    v = s.dropna().astype(int).value_counts().sort_index()
    return [{"valor": int(k), "n": int(n)} for k, n in v.items()]


def perfil(df: pd.DataFrame, localidad: str | None) -> dict:
    """Qué tipos de apartamento hay de verdad en la zona, con cuántos avisos de cada cosa."""
    g = df if not localidad else df[df["localidad"] == localidad]
    n = len(g)
    base = {"zona": localidad or "Bogotá", "n": n}
    if n == 0:
        return {**base, "mensaje": f"No hay avisos reales de {localidad} en los datos recolectados."}
    a, p, m = g["area_m2"], g["precio"], g["precio_m2"]
    cortes = [0, 40, 60, 80, 100, 130, 180, 250, 10_000]
    bins = pd.cut(a, cortes, right=False).value_counts().sort_index()
    ant = g["antiguedad"].dropna()
    tramos = pd.cut(ant, [-1, 5, 15, 30, 200], labels=["0 a 5 años", "6 a 15 años", "16 a 30 años", "más de 30 años"])
    combos = (g.dropna(subset=["estrato", "habitaciones"])
              .assign(banos=g["banos"].fillna(-1), parqueaderos=g["parqueaderos"].fillna(-1))
              .groupby(["estrato", "habitaciones", "banos", "parqueaderos"]).size()
              .sort_values(ascending=False).head(6))

    def moda(col):
        v = g[col].dropna()
        if v.empty:
            return None
        k = v.astype(int).value_counts()
        return {"valor": int(k.index[0]), "porcentaje": round(float(k.iloc[0] / len(v)), 3)}

    return {**base,
            "estratos": _conteo(g["estrato"]), "habitaciones": _conteo(g["habitaciones"]),
            "banos": _conteo(g["banos"]), "parqueaderos": _conteo(g["parqueaderos"]),
            "sin_dato": {c: int(g[c].isna().sum()) for c in ("estrato", "habitaciones", "banos", "parqueaderos", "antiguedad")},
            "area": {"min": float(a.min()), "p10": float(a.quantile(.1)), "mediana": float(a.median()),
                     "p90": float(a.quantile(.9)), "max": float(a.max()),
                     "tramos": [{"desde": int(i.left), "hasta": None if i.right >= 10_000 else int(i.right), "n": int(c)}
                                for i, c in bins.items() if c]},
            "antiguedad": {"informados": int(len(ant)),
                           "mediana": float(ant.median()) if len(ant) else None,
                           "tramos": [{"tramo": str(k), "n": int(c)} for k, c in tramos.value_counts(sort=False).items() if c]},
            "precio": {"promedio": float(p.mean()), "mediana": float(p.median()),
                       "p10": float(p.quantile(.1)), "p90": float(p.quantile(.9)), "min": float(p.min()), "max": float(p.max())},
            "m2": {"promedio": float(m.mean()), "mediana": float(m.median())},
            "comunes": {c: moda(c) for c in ("estrato", "habitaciones", "banos", "parqueaderos")},
            "tipos": [{"estrato": int(e), "habitaciones": int(h), "banos": None if b < 0 else int(b),
                       "parqueaderos": None if q < 0 else int(q), "n": int(c)}
                      for (e, h, b, q), c in combos.items()]}


# ---------------------------------------------------------------- búsqueda progresiva
def _pasos(datos: dict) -> list[dict]:
    loc = datos.get("localidad")
    vec = vecinas().get(loc, []) if loc else []
    pasos = []
    if loc:
        pasos += [
            {"clave": "exacta", "zonas": [loc], "estrato": 0, "hab": 0, "area": 0.20,
             "texto": f"en {loc}, mismo estrato, mismas habitaciones y área parecida (±20 %)"},
            {"clave": "habitaciones", "zonas": [loc], "estrato": 0, "hab": 1, "area": 0.35,
             "texto": f"en {loc}, mismo estrato, ±1 habitación y área ±35 %"},
            {"clave": "estrato", "zonas": [loc], "estrato": 1, "hab": 1, "area": 0.35,
             "texto": f"en {loc}, estrato vecino (±1), ±1 habitación y área ±35 %"},
        ]
        if vec:
            pasos.append({"clave": "vecinas", "zonas": [loc, *vec], "estrato": 0, "hab": 1, "area": 0.35,
                          "texto": f"en {loc} y sus localidades vecinas ({', '.join(vec)}), mismo estrato, ±1 habitación y área ±35 %"})
    else:
        pasos.append({"clave": "exacta", "zonas": None, "estrato": 0, "hab": 0, "area": 0.20,
                      "texto": "en toda Bogotá, mismo estrato, mismas habitaciones y área parecida (±20 %)"})
    pasos.append({"clave": "ciudad", "zonas": None, "estrato": 0, "hab": 1, "area": 0.35,
                  "texto": "en toda Bogotá, mismo estrato, ±1 habitación y área ±35 %"})
    return pasos


def _filtrar(df: pd.DataFrame, datos: dict, paso: dict) -> pd.DataFrame:
    area = float(datos["area_m2"])
    f = df["area_m2"].between(area * (1 - paso["area"]), area * (1 + paso["area"]))
    f &= (df["estrato"] - datos["estrato"]).abs() <= paso["estrato"]
    if datos.get("habitaciones") is not None:
        f &= (df["habitaciones"] - datos["habitaciones"]).abs() <= paso["hab"]
    if paso["zonas"]:
        f &= df["localidad"].isin(paso["zonas"])
    return df[f]


def _distancia(c: pd.DataFrame, datos: dict) -> pd.Series:
    """Qué tan distinto es cada comparable del apartamento consultado (0 = idéntico)."""
    d = np.abs(np.log(c["area_m2"] / float(datos["area_m2"]))) * 4
    d += (c["estrato"] - datos["estrato"]).abs() * 1.0
    d += (c["localidad"] != datos.get("localidad")).astype(float) * (0.6 if datos.get("localidad") else 0)
    for col, peso in (("habitaciones", 0.5), ("banos", 0.4), ("parqueaderos", 0.3)):
        if datos.get(col) is not None:
            d += (c[col] - datos[col]).abs().fillna(1.5) * peso
    if datos.get("antiguedad") is not None:
        d += ((c["antiguedad"] - datos["antiguedad"]).abs() / 15).fillna(0.5) * 0.3
    if datos.get("proyecto") is not None and "proyecto" in c.columns:
        d += (c["proyecto"].fillna(0) != datos["proyecto"]).astype(float) * 0.8   # nuevo vs. usado
    if datos.get("latitud") is not None and datos.get("longitud") is not None and "latitud" in c.columns:
        km = np.hypot((c["latitud"] - datos["latitud"]) * 111, (c["longitud"] - datos["longitud"]) * 110.6)
        d += (km / 2).fillna(1.0).clip(upper=3) * 0.5      # cerca del punto elegido en el mapa, mejor
    return d


def buscar(df: pd.DataFrame, datos: dict) -> dict:
    pasos, elegido, recorrido = _pasos(datos), None, []
    for paso in pasos:
        c = _filtrar(df, datos, paso)
        recorrido.append({"clave": paso["clave"], "criterio": paso["texto"], "encontrados": int(len(c))})
        log.debug("Comparables, paso %s: %d (%s)", paso["clave"], len(c), paso["texto"])
        if len(c) >= MINIMO:
            elegido = (paso, c)
            break
    if elegido is None:      # ningún paso alcanzó el mínimo: el más amplio que tenga algo
        for paso in reversed(pasos):
            c = _filtrar(df, datos, paso)
            if len(c):
                elegido = (paso, c)
                break
    if elegido is None:
        return {"pasos": recorrido, "paso": None, "candidatos": df.iloc[0:0], "usados": df.iloc[0:0]}
    paso, c = elegido
    usados = c.assign(_distancia=_distancia(c, datos)).nsmallest(MAX_USADOS, "_distancia")
    return {"pasos": recorrido, "paso": paso, "candidatos": c, "usados": usados}


# ---------------------------------------------------------------- estimación explicada
def _diferencias(u: pd.DataFrame, datos: dict) -> list[str]:
    salida, n = [], len(u)
    for col in ("estrato", "habitaciones", "banos", "parqueaderos"):
        v = datos.get(col)
        if v is None:
            continue
        distintos = int((u[col].notna() & (u[col] != v)).sum())
        sin = int(u[col].isna().sum())
        if distintos:
            valores = ", ".join(f"{int(k)} ({int(c)})" for k, c in u.loc[u[col] != v, col].dropna().astype(int)
                                .value_counts().sort_index().items())
            salida.append(f"{distintos} de {n} tienen otro número de {ETIQUETAS[col]}: {valores}")
        if sin:
            salida.append(f"{sin} de {n} no informan {ETIQUETAS[col]}")
    loc = datos.get("localidad")
    if loc:
        otros = u[u["localidad"] != loc]
        if len(otros):
            salida.append(f"{len(otros)} de {n} son de otras localidades: "
                          + ", ".join(f"{k} ({c})" for k, c in otros["localidad"].value_counts().items()))
    salida.append(f"Área de los comparables: {u['area_m2'].min():.0f} a {u['area_m2'].max():.0f} m² "
                  f"(consultado: {float(datos['area_m2']):.0f} m²)")
    return salida


def estimar(df: pd.DataFrame, datos: dict, factor_minimo: float = 1.0) -> dict:
    """Busca comparables y arma el precio con su explicación. Sin comparables: precio None.

    factor_minimo: ancho mínimo del rango (precio ÷ factor a precio × factor). La app pasa el del
    modelo (calibrado al 80 %): con 1 a 4 comparables su dispersión no dice nada de la incertidumbre
    real, y un rango de "$2.207 M a $2.207 M" daría una precisión falsa."""
    b = buscar(df, datos)
    zona = datos.get("localidad")
    en_zona = int((df["localidad"] == zona).sum()) if zona else len(df)
    u, paso = b["usados"], b["paso"]
    base = {"pasos": b["pasos"], "avisos_en_zona": en_zona, "zona": zona or "Bogotá"}
    if paso is None or len(u) == 0:
        return {**base, "viable": False, "confiabilidad": "insuficiente", "precio": None, "minimo": None, "maximo": None,
                "encontrados": 0, "usados": 0, "razones": [
                    "No se encontró ningún apartamento comparable con estas características, ni ampliando la búsqueda "
                    "a localidades vecinas y a toda Bogotá. No se da un precio para no inventarlo."],
                "comparables": u}
    area = float(datos["area_m2"])
    m2 = u["precio_m2"]
    med = float(m2.median())
    q1, q3 = float(m2.quantile(.25)), float(m2.quantile(.75))
    if len(u) < MINIMO:            # pocos: el rango se abre a todo lo observado
        q1, q3 = float(m2.min()), float(m2.max())
    dispersion = (q3 - q1) / med if med else 1
    rango_ampliado = False
    if len(u) < MINIMO and factor_minimo > 1:
        bajo, alto = med / factor_minimo, med * factor_minimo
        rango_ampliado = bajo < q1 or alto > q3
        q1, q3 = min(q1, bajo), max(q3, alto)
    estricto = paso["clave"] in ("exacta", "habitaciones")
    razones = []
    if len(u) >= IDEAL and estricto and dispersion < 0.35:
        nivel = "alta"
        razones.append(f"Hay {len(b['candidatos'])} apartamentos muy parecidos {paso['texto']}.")
        razones.append(f"Sus precios por m² son parecidos entre sí (la mitad central varía {dispersion * 100:.0f} %).")
    elif len(u) >= MINIMO and paso["clave"] != "ciudad":
        nivel = "media"
        razones.append(f"Se encontraron {len(b['candidatos'])} comparables {paso['texto']}.")
        if not estricto:
            razones.append("Hubo que ampliar la búsqueda más allá de la coincidencia estricta.")
        if dispersion >= 0.35:
            razones.append(f"Los precios por m² de los comparables varían bastante (la mitad central {dispersion * 100:.0f} %).")
        if len(u) < IDEAL:
            razones.append(f"Son menos de {IDEAL}: con más avisos la estimación sería más firme.")
    else:
        nivel = "baja"
        if len(u) < MINIMO:
            razones.append(f"Solo hay {len(u)} comparable{'s' if len(u) != 1 else ''} {paso['texto']}: "
                           "la estimación tiene poca precisión.")
        if paso["clave"] == "ciudad" and zona:
            razones.append(f"En {zona} y sus vecinas no había suficientes; se usaron avisos de toda Bogotá.")
    origen_loc = [{"localidad": k, "n": int(c)} for k, c in u["localidad"].fillna("Sin localidad").value_counts().items()]
    origen_bar = [{"barrio": k, "n": int(c)} for k, c in u["barrio"].dropna().value_counts().head(8).items()]
    considerados = ["área", "estrato"] + [ETIQUETAS[c] for c in ("habitaciones", "banos", "parqueaderos")
                                          if datos.get(c) is not None] + (["antigüedad"] if datos.get("antiguedad") is not None else [])
    if zona:
        considerados.append("localidad")
    return {**base, "viable": True, "confiabilidad": nivel, "razones": razones,
            "precio": round(med * area, -6), "minimo": round(q1 * area, -6), "maximo": round(q3 * area, -6),
            "precio_m2": med, "promedio_precio": float(u["precio"].mean()), "mediana_precio": float(u["precio"].median()),
            "promedio_m2": float(m2.mean()), "mediana_m2": med,
            "encontrados": int(len(b["candidatos"])), "usados": int(len(u)), "paso": paso["clave"],
            "criterio": paso["texto"], "amplio": not estricto, "considerados": considerados,
            "rango_ampliado": rango_ampliado,
            "origen": {"localidades": origen_loc, "barrios": origen_bar},
            "diferencias": _diferencias(u, datos), "comparables": u}
