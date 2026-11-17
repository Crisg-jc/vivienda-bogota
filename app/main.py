"""API y servidor web de la aplicación.

Ejecutar:  uvicorn app.main:app --reload        (o: python -m app)
Abrir:     http://127.0.0.1:8000

Al arrancar:
  - busca el dataset (modelo.datos.ubicar); si no hay ninguno, crea datos SINTÉTICOS de demostración;
  - si no hay modelo entrenado, o el dataset cambió desde el último entrenamiento, entrena de nuevo.
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

logging.basicConfig(level=os.environ.get("VIVIENDA_LOG", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("vivienda")
ESTATICOS = pathlib.Path(__file__).parent / "static"
ESTADO: dict = {}


def preparar_estado() -> None:
    ruta, tipo = D.ubicar()
    if ruta is None:
        log.warning("No hay dataset en datos/. Se crean datos SINTÉTICOS de demostración.")
        demo.main([])
        ruta, tipo = D.ubicar()
    art = estimar.cargar_modelo()
    if art is None or art.get("sha256") != D.huella(ruta):
        log.warning("Entrenando el modelo con %s ...", ruta.name)
        entrenar.entrenar(ruta, tipo)
        art = estimar.cargar_modelo()
    ESTADO.update(df=D.cargar(ruta), tipo=tipo, ruta=ruta, art=art, arranque=time.time(),
                  metricas=json.loads(entrenar.METRICAS.read_text(encoding="utf-8")))
    # Los datos no cambian hasta el próximo arranque: el resumen del mercado se calcula una sola vez.
    ESTADO["mercado"] = calcular_mercado(ESTADO["df"])
    log.info("Listo: %s (%s), %d avisos, modelo %s", ruta.name, tipo, len(ESTADO["df"]), art["nombre"])


@asynccontextmanager
async def ciclo(app: FastAPI):
    preparar_estado()
    yield


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
        log.info("%s %s %d %.1fms rid=%s", request.method, request.url.path, respuesta.status_code, ms, rid)
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
                                       "parqueaderos", "antiguedad", "localidad", "barrio"]}
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
    mapa = [{"lat": float(r.latitud), "lon": float(r.longitud), "m2": float(r.precio_m2), "precio": float(r.precio),
             "area": float(r.area_m2), "localidad": limpio(r.localidad)} for r in geo.itertuples()]
    return {"estratos": estratos, "estratos_omitidos": omitidos, "localidades": loc.to_dict("records"), "dispersion": dispersion, "mapa": mapa,
            "resumen": {"avisos": len(df), "mediana_precio": float(df["precio"].median()),
                        "mediana_m2": float(df["precio_m2"].median()), "mediana_area": float(df["area_m2"].median()),
                        "con_coordenadas": int(len(df.dropna(subset=["latitud", "longitud"])))}}


# ---------------------------------------------------------------- avisos

@app.get("/api/avisos")
def api_avisos(localidad: str | None = None, estrato: int | None = Query(None, ge=1, le=6),
               precio_max: float | None = None, area_min: float | None = None,
               orden: Literal["precio", "-precio", "precio_m2", "-precio_m2", "area_m2", "-area_m2"] = "precio",
               pagina: int = Query(1, ge=1), por_pagina: int = Query(25, ge=5, le=100)):
    df = ESTADO["df"]
    if localidad:
        df = df[df["localidad"] == localidad]
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
