"""API y servidor web de la aplicación.

Ejecutar:  uvicorn app.main:app --reload        (o: python -m app)
Abrir:     http://127.0.0.1:8000

Al arrancar:
  - busca el dataset (modelo.datos.ubicar); si no hay ninguno, crea datos SINTÉTICOS de demostración;
  - si no hay modelo entrenado, o el dataset cambió desde el último entrenamiento, entrena de nuevo;
  - inicia el ciclo automático de recolección (app/automatizacion.py), que recolecta, revisa cambios,
    versiona el dataset y reentrena; cuando publica un corte nuevo, la app lo carga sin reiniciarse.
"""
import json
import logging
import math
import os
import pathlib
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from limpieza.normalizar import LOCALIDADES
from modelo import datos as D
from modelo import demo, entrenar, estimar

from .automatizacion import Orquestador

logging.basicConfig(level=os.environ.get("VIVIENDA_LOG", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("vivienda")
ESTATICOS = pathlib.Path(__file__).parent / "static"
ESTADO: dict = {}
UMBRAL = 0.10        # ±10 % frente al precio esperado = "en línea con su zona"


def preparar_estado() -> None:
    """Carga dataset y modelo y precalcula los resúmenes. Arma todo aparte y lo publica al final:
    las solicitudes que llegan mientras tanto siguen viendo los datos anteriores completos."""
    ruta, tipo = D.ubicar()
    if ruta is None:
        log.warning("No hay dataset en datos/. Se crean datos SINTÉTICOS de demostración.")
        demo.main([])
        ruta, tipo = D.ubicar()
    huella = D.huella(ruta)
    art = estimar.cargar_modelo()
    if art is None or art.get("sha256") != huella:
        log.warning("Entrenando el modelo con %s ...", ruta.name)
        entrenar.entrenar(ruta, tipo)
        art = estimar.cargar_modelo()
    df = comparar_con_esperado(D.cargar(ruta), art)
    nuevo = {"df": df, "tipo": tipo, "ruta": ruta, "huella": huella, "art": art,
             "metricas": json.loads(entrenar.METRICAS.read_text(encoding="utf-8")),
             "mercado": calcular_mercado(df)}
    nuevo["zonas"] = calcular_zonas(df, art)
    ESTADO.update(nuevo)
    ESTADO.setdefault("arranque", time.time())
    log.info("Listo: %s (%s), %d avisos, modelo %s", ruta.name, tipo, len(df), art["nombre"])


def recargar_si_cambio() -> None:
    """La llama el orquestador: si el ciclo publicó un dataset nuevo, la app lo adopta."""
    ruta, _ = D.ubicar()
    if ruta is None or "ruta" not in ESTADO:
        return
    if ruta != ESTADO["ruta"] or D.huella(ruta) != ESTADO["huella"]:
        log.info("Dataset nuevo (%s): recargando datos y modelo", ruta.name)
        preparar_estado()


ORQUESTADOR = Orquestador(recargar_si_cambio)


@asynccontextmanager
async def ciclo(app: FastAPI):
    preparar_estado()
    ORQUESTADOR.iniciar()
    yield
    ORQUESTADOR.detener()


def comparar_con_esperado(df: pd.DataFrame, art: dict) -> pd.DataFrame:
    """Precio esperado de cada aviso según el modelo, calculado sin haber visto ese aviso
    (validación cruzada al entrenar), y su diferencia porcentual frente al precio pedido."""
    esperados = art.get("esperados")
    if esperados is not None and len(esperados) == len(df):
        df["precio_esperado"] = np.asarray(esperados, float)
    else:   # modelo de una versión anterior: se predice, avisando que es dentro de muestra
        df["precio_esperado"] = art["modelo"].predict(entrenar.preparar(df))
    df["diferencia_pct"] = df["precio"] / df["precio_esperado"] - 1
    df["comparacion"] = np.select([df["diferencia_pct"] < -UMBRAL, df["diferencia_pct"] > UMBRAL],
                                  ["debajo", "encima"], "en_linea")
    return df


app = FastAPI(title="Precio de apartamentos en Bogotá", version="1.1.0", lifespan=ciclo,
              description="Estimación del precio de publicación de apartamentos en venta en Bogotá D.C.")
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Política de contenido: solo recursos propios, más las teselas del mapa. Los estilos en línea
# hacen falta para los anchos de las barras que dibuja app.js.
CSP = ("default-src 'self'; img-src 'self' data: https://*.basemaps.cartocdn.com; "
       "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
CABECERAS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "strict-origin-when-cross-origin",
             "X-Frame-Options": "DENY", "Permissions-Policy": "geolocation=(), camera=(), microphone=()"}


@app.middleware("http")
async def registro_y_seguridad(request: Request, call_next):
    """Cabeceras de seguridad, identificador de solicitud y una línea de registro por solicitud."""
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    inicio = time.perf_counter()
    respuesta = await call_next(request)
    ms = (time.perf_counter() - inicio) * 1000
    respuesta.headers.update(CABECERAS)
    respuesta.headers["X-Request-ID"] = rid
    if not request.url.path.startswith("/docs") and not request.url.path.startswith("/openapi"):
        respuesta.headers["Content-Security-Policy"] = CSP
    if request.url.path.startswith("/api/"):
        respuesta.headers.setdefault("Cache-Control", "no-store")
        nivel = logging.DEBUG if request.url.path == "/api/automatizacion" else logging.INFO  # se consulta cada pocos segundos
        log.log(nivel, "%s %s %d %.1fms rid=%s", request.method, request.url.path, respuesta.status_code, ms, rid)
    return respuesta


def limpio(v):
    if v is None:
        return None
    if isinstance(v, (float, np.floating)):
        return None if math.isnan(v) else float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if v is pd.NA or v is pd.NaT:
        return None
    return v


def url_segura(u) -> str | None:
    """Solo enlaces http(s): un 'javascript:' llegado en un CSV ajeno nunca sale de la API."""
    return u if isinstance(u, str) and u.startswith(("https://", "http://")) else None


def aviso(r) -> dict:
    d = {k: limpio(r.get(k)) for k in ["precio", "area_m2", "precio_m2", "habitaciones", "banos", "estrato",
                                       "parqueaderos", "antiguedad", "localidad", "barrio", "precio_esperado",
                                       "diferencia_pct", "comparacion", "precio_anterior"]}
    d["url_origen"] = url_segura(r.get("url_origen"))
    return d


@app.get("/api/salud", include_in_schema=False)
def api_salud():
    """Para Docker, el balanceador o el monitoreo: 200 cuando el dataset y el modelo están cargados."""
    if "art" not in ESTADO:
        raise HTTPException(503, "Cargando")
    return {"estado": "ok", "avisos": len(ESTADO["df"]), "modelo": ESTADO["art"]["nombre"],
            "segundos_activo": round(time.time() - ESTADO["arranque"])}


# ---------------------------------------------------------------- estado general

@app.get("/api/estado")
def api_estado():
    m = ESTADO["metricas"]
    return {
        "dataset": {"archivo": ESTADO["ruta"].name, "tipo": ESTADO["tipo"], "avisos": len(ESTADO["df"])},
        "modelo": {"nombre": m["modelo_elegido"], "supera_linea_base": m["supera_linea_base"],
                   "entrenado": m["fecha"], "nivel_rango": m["rango"]["nivel"]},
        "localidades": LOCALIDADES,
        "referencia": ESTADO["art"]["referencia"],
    }


# ---------------------------------------------------------------- estimación

class Entrada(BaseModel):
    area_m2: float = Field(ge=15, le=1000)
    estrato: int = Field(ge=1, le=6)
    localidad: str | None = None
    habitaciones: int | None = Field(default=None, ge=0, le=10)
    banos: int | None = Field(default=None, ge=0, le=10)
    parqueaderos: int | None = Field(default=None, ge=0, le=10)
    antiguedad: int | None = Field(default=None, ge=0, le=100)

    @field_validator("localidad")
    @classmethod
    def localidad_valida(cls, v):
        if v in (None, ""):
            return None
        if v not in LOCALIDADES:
            raise ValueError("Localidad desconocida. Use uno de los nombres de /api/estado.")
        return v


@app.post("/api/estimar")
def api_estimar(e: Entrada):
    datos = e.model_dump()
    res = estimar.estimar(ESTADO["art"], datos)
    df = ESTADO["df"]
    mismo = df.loc[df["estrato"] == e.estrato, "precio_m2"].dropna()
    res["contexto"] = {
        "estrato_avisos": int(len(mismo)),
        "estrato_mediana_m2": float(mismo.median()) if len(mismo) else None,
        "percentil_m2": round(float((mismo < res["precio_m2"]).mean()) * 100) if len(mismo) else None,
    }
    res["similares"] = [aviso(r) for r in estimar.similares(df, datos).to_dict("records")]
    z = next((z for z in ESTADO["zonas"]["localidades"] if z["nombre"] == e.localidad and z["n"]), None)
    res["zona"] = None if z is None else {
        "localidad": z["nombre"], "n": z["n"], "mediana_m2": z["mediana_m2"],
        "diferencia_m2_pct": res["precio_m2"] / z["mediana_m2"] - 1, "p10_precio": z["p10_precio"],
        "p90_precio": z["p90_precio"]}
    res["modelo"] = ESTADO["art"]["nombre"]
    res["demo"] = ESTADO["tipo"] == "demo"
    return res


# ---------------------------------------------------------------- mercado

def caja(s: pd.Series) -> dict:
    s = s.dropna()
    q1, med, q3 = (float(s.quantile(p)) for p in (0.25, 0.5, 0.75))
    riq = q3 - q1
    dentro = s[(s >= q1 - 1.5 * riq) & (s <= q3 + 1.5 * riq)]
    return {"n": int(len(s)), "q1": q1, "mediana": med, "q3": q3,
            "min": float(dentro.min()), "max": float(dentro.max()),
            "atipicos": [float(v) for v in s[(s < q1 - 1.5 * riq) | (s > q3 + 1.5 * riq)]][:60],
            "puntos": [float(v) for v in s.sample(min(len(s), 150), random_state=1)]}


@app.get("/api/mercado")
def api_mercado():
    return ESTADO["mercado"]


def calcular_mercado(df: pd.DataFrame) -> dict:
    grupos = df.dropna(subset=["estrato"]).groupby("estrato")
    estratos = [{"estrato": int(k), **caja(g["precio_m2"])} for k, g in grupos if len(g) >= 3]
    omitidos = [{"estrato": int(k), "n": int(len(g))} for k, g in grupos if len(g) < 3]
    loc = (df.dropna(subset=["localidad"]).groupby("localidad")
           .agg(n=("precio", "size"), mediana_m2=("precio_m2", "median"), mediana_precio=("precio", "median"))
           .reset_index().sort_values("mediana_m2", ascending=False))
    muestra = df.sample(min(len(df), 1500), random_state=2)
    dispersion = [{"area": float(r.area_m2), "precio": float(r.precio),
                   "estrato": None if pd.isna(r.estrato) else int(r.estrato)} for r in muestra.itertuples()]
    geo = muestra.dropna(subset=["latitud", "longitud"])
    geo = df.dropna(subset=["latitud", "longitud"])
    if len(geo) > 5000:
        geo = geo.sample(5000, random_state=2)
    mapa = [{"lat": float(r.latitud), "lon": float(r.longitud), "m2": float(r.precio_m2), "precio": float(r.precio),
             "area": float(r.area_m2), "localidad": limpio(r.localidad), "barrio": limpio(r.barrio),
             "estrato": None if pd.isna(r.estrato) else int(r.estrato), "url": url_segura(r.url_origen),
             "diferencia_pct": round(float(r.diferencia_pct), 4), "comparacion": r.comparacion}
            for r in geo.itertuples()]
    return {"estratos": estratos, "estratos_omitidos": omitidos, "localidades": loc.to_dict("records"), "dispersion": dispersion, "mapa": mapa,
            "resumen": {"avisos": len(df), "mediana_precio": float(df["precio"].median()),
                        "mediana_m2": float(df["precio_m2"].median()), "mediana_area": float(df["area_m2"].median()),
                        "con_coordenadas": int(len(df.dropna(subset=["latitud", "longitud"])))}}


# ---------------------------------------------------------------- zonas

def estadisticas(g: pd.DataFrame) -> dict:
    p, m = g["precio"], g["precio_m2"]
    return {"n": int(len(g)), "promedio_precio": float(p.mean()), "mediana_precio": float(p.median()),
            "p10_precio": float(p.quantile(0.1)), "p90_precio": float(p.quantile(0.9)),
            "promedio_m2": float(m.mean()), "mediana_m2": float(m.median()),
            "p10_m2": float(m.quantile(0.1)), "p90_m2": float(m.quantile(0.9)),
            "mediana_area": float(g["area_m2"].median()),
            "por_debajo_pct": float((g["comparacion"] == "debajo").mean()),
            "por_encima_pct": float((g["comparacion"] == "encima").mean())}


def tipico_de(g: pd.DataFrame, nombre: str, art: dict) -> dict:
    """Valor estimado de un apartamento típico de la zona: área, habitaciones, baños,
    parqueaderos y estrato más comunes allí, pasados por el modelo con su rango."""
    def mediana(c):
        v = g[c].median()
        return None if pd.isna(v) else int(round(v))
    estrato_moda = g["estrato"].mode()
    datos = {"area_m2": float(round(g["area_m2"].median())), "localidad": nombre,
             "estrato": int(estrato_moda.iat[0]) if len(estrato_moda) else 3,
             "habitaciones": mediana("habitaciones"), "banos": mediana("banos"),
             "parqueaderos": mediana("parqueaderos"), "antiguedad": mediana("antiguedad")}
    r = estimar.estimar(art, datos)
    return {"datos": datos, "precio": r["precio"], "minimo": r["minimo"], "maximo": r["maximo"]}


def calcular_zonas(df: pd.DataFrame, art: dict) -> dict:
    ciudad = estadisticas(df)
    filas = []
    for nombre in LOCALIDADES + ["Sumapaz"]:
        g = df[df["localidad"] == nombre]
        if len(g) == 0:
            filas.append({"nombre": nombre, "n": 0})
            continue
        z = {"nombre": nombre, **estadisticas(g)}
        z["vs_ciudad_pct"] = z["mediana_m2"] / ciudad["mediana_m2"] - 1
        z["suficiente"] = len(g) >= 10
        filas.append(z)
    con_datos = sorted((z for z in filas if z["n"] >= 3), key=lambda z: -z["mediana_m2"])
    for i, z in enumerate(con_datos, 1):
        z["puesto"] = i
    return {"ciudad": ciudad, "localidades": filas, "con_ranking": len(con_datos), "umbral": UMBRAL}


@app.get("/api/zonas")
def api_zonas():
    return ESTADO["zonas"]


@app.get("/api/zonas/{nombre}")
def api_zona(nombre: str):
    z = next((z for z in ESTADO["zonas"]["localidades"] if z["nombre"] == nombre), None)
    if z is None:
        raise HTTPException(404, "Localidad desconocida.")
    df = ESTADO["df"]
    g = df[df["localidad"] == nombre]
    if len(g) == 0:
        return {"zona": z, "barrios": [], "tipico": None, "debajo": [], "encima": []}
    barrios = []
    for b, gb in g.dropna(subset=["barrio"]).groupby("barrio"):
        fila = {"barrio": b, "n": int(len(gb)), "mediana_m2": float(gb["precio_m2"].median()),
                "mediana_precio": float(gb["precio"].median())}
        fila["vs_localidad_pct"] = fila["mediana_m2"] / z["mediana_m2"] - 1
        barrios.append(fila)
    barrios.sort(key=lambda b: (-b["n"], b["barrio"]))
    orden = g.sort_values("diferencia_pct")
    return {"zona": z, "barrios": barrios[:40],
            "tipico": tipico_de(g, nombre, ESTADO["art"]),
            "debajo": [aviso(r) for r in orden[orden["comparacion"] == "debajo"].head(5).to_dict("records")],
            "encima": [aviso(r) for r in orden[orden["comparacion"] == "encima"].tail(5).iloc[::-1].to_dict("records")]}


# ---------------------------------------------------------------- avisos

@app.get("/api/avisos")
def api_avisos(localidad: str | None = None, estrato: int | None = Query(None, ge=1, le=6),
               precio_max: float | None = None, area_min: float | None = None, barrio: str | None = None,
               comparacion: Literal["debajo", "en_linea", "encima"] | None = None,
               orden: Literal["precio", "-precio", "precio_m2", "-precio_m2", "area_m2", "-area_m2",
                              "diferencia_pct", "-diferencia_pct"] = "precio",
               pagina: int = Query(1, ge=1), por_pagina: int = Query(25, ge=5, le=100)):
    df = ESTADO["df"]
    if localidad:
        df = df[df["localidad"] == localidad]
    if barrio:
        df = df[df["barrio"] == barrio]
    if comparacion:
        df = df[df["comparacion"] == comparacion]
    if estrato:
        df = df[df["estrato"] == estrato]
    if precio_max:
        df = df[df["precio"] <= precio_max]
    if area_min:
        df = df[df["area_m2"] >= area_min]
    col = orden.lstrip("-")
    df = df.sort_values(col, ascending=not orden.startswith("-"))
    total = len(df)
    pag = df.iloc[(pagina - 1) * por_pagina: pagina * por_pagina]
    return {"total": total, "pagina": pagina, "por_pagina": por_pagina,
            "avisos": [aviso(r) for r in pag.to_dict("records")]}


# ---------------------------------------------------------------- automatización

def es_local(request: Request) -> bool:
    token = os.environ.get("VIVIENDA_TOKEN_ADMIN")
    if token and request.headers.get("x-token-admin") == token:
        return True
    return bool(request.client) and request.client.host in ("127.0.0.1", "::1", "localhost", "testclient")


@app.get("/api/automatizacion")
def api_automatizacion(request: Request):
    return {**ORQUESTADOR.estado(), "puede_ejecutar": es_local(request),
            "dataset": {"archivo": ESTADO["ruta"].name, "tipo": ESTADO["tipo"], "avisos": len(ESTADO["df"])}}


@app.post("/api/automatizacion/ejecutar")
def api_automatizacion_ejecutar(request: Request):
    """Solo desde el mismo computador (o con VIVIENDA_TOKEN_ADMIN): nadie de afuera dispara visitas al portal."""
    if not es_local(request):
        raise HTTPException(403, "Solo se puede iniciar un ciclo desde el computador donde corre la aplicación.")
    ok, mensaje = ORQUESTADOR.ejecutar_ahora()
    if not ok:
        raise HTTPException(409, mensaje)
    return {"mensaje": mensaje}


# ---------------------------------------------------------------- modelo

@app.get("/api/modelo")
def api_modelo():
    return ESTADO["metricas"]


# ---------------------------------------------------------------- interfaz

class Estaticos(StaticFiles):
    """Archivos de la interfaz con caché de una hora (las fuentes y Leaflet, un día)."""

    async def get_response(self, path, scope):
        r = await super().get_response(path, scope)
        if r.status_code == 200:
            r.headers["Cache-Control"] = f"public, max-age={86400 if path.startswith('vendor/') else 3600}"
        return r


app.mount("/static", Estaticos(directory=ESTATICOS), name="static")


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def inicio():
    return FileResponse(ESTATICOS / "index.html")


@app.exception_handler(KeyError)
async def sin_estado(request: Request, exc: KeyError):
    """Un KeyError aquí significa que el arranque no terminó: se responde 503 sin filtrar detalles internos."""
    log.error("KeyError en %s: %s", request.url.path, exc)
    return JSONResponse({"detail": "La aplicación todavía no cargó el dataset ni el modelo."}, status_code=503)
