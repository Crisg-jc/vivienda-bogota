"""API y servidor web de la aplicación.

Ejecutar:  uvicorn app.main:app --reload        (o: python -m app)
Abrir:     http://127.0.0.1:8000

Al arrancar:
  - busca el dataset (modelo.datos.ubicar); si no hay ninguno, crea datos SINTÉTICOS de demostración;
  - si no hay modelo entrenado, o el dataset cambió desde el último entrenamiento, entrena de nuevo;
  - inicia el ciclo automático de recolección (app/automatizacion.py), que recolecta, revisa cambios,
    versiona el dataset y reentrena; cuando publica un corte nuevo, la app lo carga sin reiniciarse.
"""
import datetime as dt
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
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from comun import registro
from comun.registro import cronometro
from limpieza.normalizar import LOCALIDADES
from modelo import comparables, demo, entrenar, estimar, precision
from modelo import datos as D
from scraper import db

from .automatizacion import Orquestador

registro.configurar("app")      # si ya lo hizo `python -m app`, no hace nada
log = logging.getLogger("vivienda.app")
log_api = logging.getLogger("vivienda.api")
log_nav = logging.getLogger("vivienda.navegador")
ESTATICOS = pathlib.Path(__file__).parent / "static"
ESTADO: dict = {}
UMBRAL = 0.10        # ±10 % frente al precio esperado = "en línea con su zona"


def asegurar_dataset_real() -> None:
    """Si la base ya tiene avisos recolectados pero en datos/ no hay ningún dataset_vN.csv (base
    copiada de otra carpeta, o un ciclo que no alcanzó a publicar), se construye ahora mismo a
    partir de la base, sin visitar ningún portal."""
    ruta, tipo = D.ubicar()
    if tipo == "real" or os.environ.get("VIVIENDA_DATASET"):
        return
    try:
        eng = motor_bd()
        with eng.begin() as c:
            n = c.scalar(select(func.count()).select_from(db.inmuebles_crudos)) or 0
    except Exception:
        log.exception("No se pudo revisar la base de datos para construir el dataset real")
        return
    if n == 0:
        log.debug("La base no tiene avisos recolectados todavía: se espera al primer ciclo de recolección")
        return
    log.warning("La base tiene %d avisos recolectados pero no hay dataset_vN.csv en datos/: se construye ahora", n)
    from limpieza import construir_dataset as cd
    try:
        with cronometro(log, "Construir dataset real desde la base"):
            corte = cd.construir(notas="Construido al abrir la aplicación", eng=eng)
        log.info("Dataset real construido: %s", corte)
    except SystemExit as e:
        log.warning("No se pudo construir el dataset real: %s", e)
    except Exception:
        log.exception("Falló la construcción del dataset real")


_MOTOR = {}


def motor_bd():
    if "eng" not in _MOTOR:
        _MOTOR["eng"] = db.motor()
    return _MOTOR["eng"]


def sin_datos(motivo: str, ruta=None) -> None:
    """Estado sin datos reales suficientes. La app NO inventa resultados: la interfaz muestra
    el avance de la recolección y las estimaciones esperan a que haya avisos reales."""
    log.warning("Sin datos reales suficientes: %s", motivo)
    ESTADO.clear()
    ESTADO.update({"tipo": "ninguno", "motivo": motivo, "ruta": ruta, "huella": None, "arranque": time.time()})


def preparar_estado() -> None:
    """Carga dataset y modelo y precalcula los resúmenes. Arma todo aparte y lo publica al final:
    las solicitudes que llegan mientras tanto siguen viendo los datos anteriores completos."""
    t0 = time.perf_counter()
    log.info("Preparando datos y modelo ...")
    asegurar_dataset_real()
    ruta, tipo = D.ubicar()
    log.info("Dataset elegido: %s (tipo %s)", ruta, tipo)
    if ruta is None and os.environ.get("VIVIENDA_DEMO") == "1":
        log.warning("VIVIENDA_DEMO=1: se crean datos SINTÉTICOS de demostración (solo para pruebas)")
        with cronometro(log, "Crear datos de demostración"):
            demo.main([])
        ruta, tipo = D.ubicar()
    if ruta is None:
        sin_datos("Todavía no se ha recolectado ningún aviso real. La recolección automática está en marcha.")
        actualizar_traza()
        return
    with cronometro(log, "Calcular huella del dataset", logging.DEBUG):
        huella = D.huella(ruta)
    log.debug("Huella sha256 del dataset: %s", huella)
    with cronometro(log, "Cargar modelo guardado", logging.DEBUG):
        art = estimar.cargar_modelo()
    if art is None:
        log.warning("No hay modelo entrenado en artefactos/: se entrena ahora con %s", ruta.name)
    elif art.get("sha256") != huella:
        log.warning("El modelo guardado se entrenó con otro dataset (huella %s…, actual %s…): se reentrena",
                    str(art.get("sha256"))[:10], huella[:10])
    elif art.get("version") != entrenar.VERSION_MODELO:
        log.warning("El modelo guardado es de una versión anterior del código (%s, actual %s): se reentrena",
                    art.get("version"), entrenar.VERSION_MODELO)
    else:
        log.info("Modelo guardado vigente: %s", art.get("nombre"))
    if art is None or art.get("sha256") != huella or art.get("version") != entrenar.VERSION_MODELO:
        try:
            with cronometro(log, f"Entrenar el modelo con {ruta.name}"):
                entrenar.entrenar(ruta, tipo)
        except SystemExit as e:
            sin_datos(f"Hay muy pocos avisos reales para estimar con confianza ({e}). "
                      "La recolección automática sigue sumando avisos.", ruta)
            ESTADO["huella"] = huella
            actualizar_traza()
            return
        art = estimar.cargar_modelo()
    with cronometro(log, "Cargar dataset en memoria", logging.DEBUG):
        df = D.cargar(ruta)
    log.info("Dataset cargado: %d filas, %d columnas", len(df), df.shape[1])
    log.debug("Columnas: %s", list(df.columns))
    nulos = {c: int(n) for c, n in df.isna().sum().items() if n}
    if nulos:
        log.debug("Valores vacíos por columna: %s", nulos)
    with cronometro(log, "Comparar cada aviso con su precio esperado", logging.DEBUG):
        df = comparar_con_esperado(df, art)
    log.info("Comparación con el esperado: %s", df["comparacion"].value_counts().to_dict())
    nuevo = {"df": df, "tipo": tipo, "ruta": ruta, "huella": huella, "art": art}
    with cronometro(log, "Leer métricas del modelo", logging.DEBUG):
        nuevo["metricas"] = json.loads(entrenar.METRICAS.read_text(encoding="utf-8"))
    with cronometro(log, "Calcular resúmenes de mercado", logging.DEBUG):
        nuevo["mercado"] = calcular_mercado(D.utiles(df))
    with cronometro(log, "Calcular estadísticas por zona", logging.DEBUG):
        nuevo["zonas"] = calcular_zonas(D.utiles(df), art)
    sin_loc = [z["nombre"] for z in nuevo["zonas"]["localidades"] if not z["n"]]
    if sin_loc:
        log.info("Localidades sin avisos en este dataset: %s", ", ".join(sin_loc))
    for k in ("motivo",):
        ESTADO.pop(k, None)
    ESTADO.update(nuevo)
    ESTADO.setdefault("arranque", time.time())
    actualizar_traza()
    log.info("Listo en %.1fs: %s (%s), %d avisos, modelo %s", time.perf_counter() - t0, ruta.name, tipo,
             len(df), art["nombre"])


# ---------------------------------------------------------------- trazabilidad en vivo
# El dataset es un corte para el modelo; la verificación contra la fuente cambia a cada rato.
# Por eso las fechas de cada aviso (primera vez visto, última verificación, último cambio,
# disponibilidad) se leen de la base en vivo y se refrescan cada 20 s mientras la app está abierta.
TRAZA: dict = {"por_url": {}, "leida": None, "resumen": {}}


def _iso(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, str):
        return v
    return v.astimezone().isoformat(timespec="seconds") if hasattr(v, "astimezone") else str(v)


def actualizar_traza() -> None:
    try:
        eng = motor_bd()
        t = db.inmuebles_crudos.c
        with eng.begin() as c:
            filas = c.execute(select(t.url_origen, t.portal, t.publicado_txt, t.primera_vez, t.verificado_en,
                                     t.actualizado_en, t.fecha_consulta, t.disponible, t.retirado_en)).all()
    except Exception:
        log.exception("No se pudo leer la trazabilidad de los avisos desde la base")
        return
    from limpieza.normalizar import fecha as fecha_txt
    ahora = dt.datetime.now()
    vigencia = dt.timedelta(days=dias_vigencia())
    por_url, vigentes, viejos, retirados, ultima = {}, 0, 0, 0, None
    for f in filas:
        # Registros de versiones anteriores sin verificado_en: la última visita conocida es fecha_consulta.
        verificado = f.verificado_en or f.actualizado_en or (
            dt.datetime.combine(f.fecha_consulta, dt.time()) if f.fecha_consulta else None)
        disponible = f.disponible is not False
        vigente = bool(disponible and verificado and ahora - verificado <= vigencia)
        if not disponible:
            retirados += 1
        elif vigente:
            vigentes += 1
        else:
            viejos += 1
        if verificado and (ultima is None or verificado > ultima):
            ultima = verificado
        publicado = fecha_txt(f.publicado_txt)
        por_url[f.url_origen] = {
            "portal": f.portal, "publicado": publicado.isoformat() if publicado else None,
            "primera_vez": _iso(f.primera_vez), "verificado_en": _iso(verificado),
            "actualizado_en": _iso(f.actualizado_en), "disponible": disponible,
            "retirado_en": _iso(f.retirado_en), "vigente": vigente}
    TRAZA.update(por_url=por_url, leida=ahora, resumen={
        "registros": len(filas), "vigentes": vigentes, "sin_verificar_reciente": viejos, "retirados": retirados,
        "ultima_verificacion": _iso(ultima), "dias_vigencia": dias_vigencia()})
    log.debug("Trazabilidad actualizada: %s", TRAZA["resumen"])


def dias_vigencia() -> int:
    try:
        from scraper import ciclo as cy
        from scraper.recolectar import cargar_conf
        return int(cy.ajustes(cargar_conf())["dias_vigencia"])
    except Exception:
        return 7


def recargar_si_cambio() -> None:
    """La llama el orquestador cada 20 s: refresca la trazabilidad y, si el ciclo publicó un
    dataset nuevo, la app lo adopta sin reiniciarse."""
    actualizar_traza()
    if "tipo" not in ESTADO:
        return
    if ESTADO["tipo"] == "ninguno":
        asegurar_dataset_real()
    ruta, _ = D.ubicar()
    if ruta is None:
        log.debug("Revisión de dataset: todavía no hay dataset real")
        return
    huella = D.huella(ruta)
    if ruta == ESTADO.get("ruta") and huella == ESTADO.get("huella"):
        log.debug("Revisión de dataset: sin cambios (%s)", ruta.name)
        return
    log.info("Dataset nuevo (%s, antes %s): recargando datos y modelo", ruta.name,
             ESTADO["ruta"].name if ESTADO.get("ruta") else "ninguno")
    try:
        preparar_estado()
    except (Exception, SystemExit):
        # Un SystemExit en este hilo lo mataría en silencio: se registra y se siguen usando los datos anteriores.
        log.exception("No se pudo cargar el dataset nuevo %s; se siguen usando los datos anteriores", ruta.name)
        ESTADO["ruta"], ESTADO["huella"] = ruta, huella      # no reintentar el mismo archivo cada 20 s


ORQUESTADOR = Orquestador(recargar_si_cambio)


@asynccontextmanager
async def ciclo(app: FastAPI):
    log.info("Arranque del servidor web")
    try:
        preparar_estado()
    except Exception:
        log.critical("No se pudo preparar el dataset o el modelo: la aplicación no puede responder. "
                     "Ejecute `python -m app.diagnostico` para ver la causa.", exc_info=True)
        raise
    ORQUESTADOR.iniciar()
    yield
    log.info("Apagando: deteniendo el ciclo automático")
    ORQUESTADOR.detener()
    log.info("Servidor detenido")


def comparar_con_esperado(df: pd.DataFrame, art: dict) -> pd.DataFrame:
    """Precio esperado de cada aviso según el modelo, calculado sin haber visto ese aviso
    (validación cruzada al entrenar), y su diferencia porcentual frente al precio pedido."""
    esperados = art.get("esperados")
    if esperados is not None and len(esperados) == len(df):
        log.debug("Precio esperado: se usan las predicciones fuera de muestra guardadas (%d)", len(esperados))
        df["precio_esperado"] = np.asarray(esperados, float)
    else:   # modelo de una versión anterior: se predice, avisando que es dentro de muestra
        log.warning("El modelo no trae predicciones fuera de muestra para estas %d filas (trae %s): "
                    "se predice dentro de muestra", len(df), None if esperados is None else len(esperados))
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
    rid = (request.headers.get("x-request-id") or uuid.uuid4().hex[:12])[:40]
    marca = registro.RID.set(rid)
    inicio = time.perf_counter()
    ruta = request.url.path
    sondeo = ruta in ("/api/automatizacion", "/api/salud", "/api/registro-cliente")   # frecuentes: solo en DEBUG
    if ruta.startswith("/api/") and not sondeo:
        log_api.debug("INICIO %s %s%s cliente=%s navegador=%s", request.method, ruta,
                      f"?{request.url.query}" if request.url.query else "",
                      request.client.host if request.client else "?", request.headers.get("user-agent", "-")[:160])
    try:
        respuesta = await call_next(request)
    except Exception:
        log_api.exception("Error no atrapado en %s %s tras %.1fms", request.method, ruta,
                          (time.perf_counter() - inicio) * 1000)
        respuesta = JSONResponse({"detail": "Error interno. Revise datos/registros/errores.log.", "rid": rid},
                                 status_code=500)
    ms = (time.perf_counter() - inicio) * 1000
    respuesta.headers.update(CABECERAS)
    respuesta.headers["X-Request-ID"] = rid
    if not request.url.path.startswith("/docs") and not request.url.path.startswith("/openapi"):
        respuesta.headers["Content-Security-Policy"] = CSP
    if ruta.startswith("/api/"):
        respuesta.headers.setdefault("Cache-Control", "no-store")
        codigo = respuesta.status_code
        nivel = (logging.ERROR if codigo >= 500 else logging.WARNING if codigo >= 400
                 else logging.DEBUG if sondeo else logging.INFO)
        lento = "  LENTA" if ms > 1500 else ""
        log_api.log(nivel, "%s %s -> %d en %.1fms rid=%s%s", request.method, ruta, codigo, ms, rid, lento)
    elif respuesta.status_code >= 400:
        # Chrome pide estos archivos solo cuando las herramientas de desarrollo (F12) están abiertas.
        ruido = ruta.startswith("/.well-known/") or ruta.endswith(".map")
        log_api.log(logging.DEBUG if ruido else logging.WARNING, "%s %s -> %d (archivo de la interfaz)",
                    request.method, ruta, respuesta.status_code)
    else:
        log_api.debug("%s %s -> %d en %.1fms", request.method, ruta, respuesta.status_code, ms)
    registro.RID.reset(marca)
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
                                       "diferencia_pct", "comparacion", "precio_anterior", "latitud", "longitud",
                                       "atipico", "piso", "administracion"]}
    d["proyecto"] = bool(r.get("proyecto")) if r.get("proyecto") is not None and not pd.isna(r.get("proyecto")) else None
    d["url_origen"] = url_segura(r.get("url_origen"))
    # Trazabilidad en vivo: fuente, publicación, primera vez visto, última verificación y último cambio.
    t = TRAZA["por_url"].get(r.get("url_origen"))
    d["fuente"] = t or {"portal": limpio(r.get("portal")), "publicado": None, "primera_vez": None,
                        "verificado_en": None, "actualizado_en": None, "disponible": None, "vigente": False}
    return d


def listo() -> None:
    """Las consultas que necesitan datos reales responden 503 con el motivo mientras no los hay:
    nunca se contesta con datos inventados."""
    if ESTADO.get("tipo") == "ninguno":
        raise HTTPException(503, ESTADO.get("motivo") or "Todavía no hay datos reales suficientes.")


def info_dataset() -> dict:
    if ESTADO.get("tipo") == "ninguno":
        return {"archivo": ESTADO["ruta"].name if ESTADO.get("ruta") else None, "tipo": "ninguno", "avisos": 0,
                "motivo": ESTADO.get("motivo")}
    return {"archivo": ESTADO["ruta"].name, "tipo": ESTADO["tipo"], "avisos": len(ESTADO["df"])}


@app.get("/api/salud", include_in_schema=False)
def api_salud():
    """Para Docker, el balanceador o el monitoreo: 200 cuando la app terminó de arrancar."""
    if ESTADO.get("tipo") == "ninguno":
        return {"estado": "sin_datos", "motivo": ESTADO.get("motivo"),
                "segundos_activo": round(time.time() - ESTADO["arranque"])}
    if "art" not in ESTADO:
        raise HTTPException(503, "Cargando")
    return {"estado": "ok", "avisos": len(ESTADO["df"]), "modelo": ESTADO["art"]["nombre"],
            "segundos_activo": round(time.time() - ESTADO["arranque"])}


# ---------------------------------------------------------------- estado general

@app.get("/api/estado")
def api_estado():
    verificacion = TRAZA["resumen"]
    if ESTADO.get("tipo") == "ninguno":
        return {"dataset": info_dataset(), "modelo": None, "localidades": LOCALIDADES, "referencia": None,
                "verificacion": verificacion}
    m = ESTADO["metricas"]
    return {
        "verificacion": verificacion,
        "dataset": info_dataset(),
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
    piso: int | None = Field(default=None, ge=1, le=60)
    administracion: int | None = Field(default=None, ge=0, le=10_000_000)
    # Punto del mapa (lo que el usuario tocó o el centro de la localidad): da el m² de los vecinos
    latitud: float | None = Field(default=None, ge=4.4, le=4.9)
    longitud: float | None = Field(default=None, ge=-74.3, le=-73.9)

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
    """Primero mira qué hay de verdad en la zona y busca apartamentos comparables; el precio sale de
    ellos (mediana del m² × área) y el modelo queda como contraste. Sin comparables no hay precio."""
    listo()
    datos = e.model_dump()
    t = time.perf_counter()
    log_api.debug("Estimar con: %s", datos)
    datos["proyecto"] = 1.0 if e.antiguedad == 0 else (0.0 if e.antiguedad is not None else None)
    df = para_comparar(ESTADO["df"])
    comp = comparables.estimar(df, datos, factor_minimo=math.exp(ESTADO["art"].get("q_log", 0)))
    usados = comp.pop("comparables")
    log_api.debug("Comparables: %s", {k: v for k, v in comp.items() if k not in ("diferencias", "origen")})
    res = estimar.estimar(ESTADO["art"], datos)          # efectos, advertencias y referencia del modelo
    modelo = {"nombre": ESTADO["art"]["nombre"], "precio": res["precio"], "minimo": res["minimo"],
              "maximo": res["maximo"], "precio_m2": res["precio_m2"]}
    if comp["viable"]:
        # Mezcla medida con avisos reales (modelo/precision.py): según el nivel de confiabilidad, cuánto
        # pesan los comparables y cuánto el modelo, y qué ancho de rango acierta 8 de cada 10 veces.
        mezcla = precision.combinar(ESTADO["metricas"].get("precision"), comp["confiabilidad"], comp["precio"],
                                    modelo["precio"], math.exp(ESTADO["art"]["q_log"]))
        res.update(precio=mezcla["precio"], minimo=mezcla["minimo"], maximo=mezcla["maximo"],
                   precio_m2=round(mezcla["precio"] / e.area_m2, -3))
        comp["mezcla"] = mezcla
        modelo["diferencia_pct"] = modelo["precio"] / comp["precio"] - 1 if comp["precio"] else None
    else:
        res.update(precio=None, minimo=None, maximo=None, precio_m2=None)
    res["comparables"] = comp
    res["modelo"] = modelo
    res["similares"] = [aviso(r) for r in usados.to_dict("records")]
    mismo = df.loc[df["estrato"] == e.estrato, "precio_m2"].dropna()
    res["contexto"] = {
        "estrato_avisos": int(len(mismo)),
        "estrato_mediana_m2": float(mismo.median()) if len(mismo) else None,
        "percentil_m2": round(float((mismo < res["precio_m2"]).mean()) * 100) if len(mismo) and res["precio_m2"] else None,
    }
    z = next((z for z in ESTADO["zonas"]["localidades"] if z["nombre"] == e.localidad and z["n"]), None)
    res["zona"] = None if z is None or not res["precio_m2"] else {
        "localidad": z["nombre"], "n": z["n"], "mediana_m2": z["mediana_m2"],
        "diferencia_m2_pct": res["precio_m2"] / z["mediana_m2"] - 1, "p10_precio": z["p10_precio"],
        "p90_precio": z["p90_precio"]}
    res["demo"] = ESTADO["tipo"] == "demo"
    if e.localidad and comp["confiabilidad"] in ("baja", "insuficiente"):
        # El recolector pondrá primero en la cola los avisos de esta localidad.
        try:
            from scraper import ciclo as cy
            cy.anotar_demanda(e.localidad)
        except OSError:
            log_api.warning("No se pudo anotar la demanda de %s", e.localidad, exc_info=True)
    if comp["viable"]:
        log_api.info("Estimación: %s m², estrato %s, %s -> $%s (rango $%s–$%s); %d comparables de %d (%s), "
                     "confiabilidad %s; modelo %s $%s; %.0fms", e.area_m2, e.estrato, e.localidad or "sin localidad",
                     f"{res['precio']:,.0f}", f"{res['minimo']:,.0f}", f"{res['maximo']:,.0f}", comp["usados"],
                     comp["encontrados"], comp["paso"], comp["confiabilidad"], modelo["nombre"],
                     f"{modelo['precio']:,.0f}", (time.perf_counter() - t) * 1000)
    else:
        log_api.info("Sin estimación: %s m², estrato %s, %s -> sin comparables (%s)", e.area_m2, e.estrato,
                     e.localidad or "sin localidad", comp["pasos"])
    return res


def para_comparar(df: pd.DataFrame) -> pd.DataFrame:
    """Avisos que sirven de comparables: publicados todavía y no marcados como atípicos."""
    return D.utiles(vigentes(df))


def vigentes(df: pd.DataFrame) -> pd.DataFrame:
    """Sin los avisos que el portal ya retiró (la base lo sabe antes que el corte del dataset)."""
    retirados = {u for u, t in TRAZA["por_url"].items() if not t["disponible"]}
    return df[~df["url_origen"].isin(retirados)] if retirados else df


_PERFILES: dict = {}


@app.get("/api/perfil")
def api_perfil(localidad: str | None = None):
    """Qué tipos de apartamento existen de verdad en una localidad (o en Bogotá) y cuántos hay de cada uno."""
    listo()
    if localidad and localidad not in LOCALIDADES:
        raise HTTPException(404, "Localidad desconocida.")
    clave = (ESTADO["huella"], localidad, len(TRAZA["por_url"]))
    if clave not in _PERFILES:
        if len(_PERFILES) > 60:
            _PERFILES.clear()
        _PERFILES[clave] = comparables.perfil(para_comparar(ESTADO["df"]), localidad)
        log_api.debug("Perfil calculado para %s: %d avisos", localidad or "Bogotá", _PERFILES[clave]["n"])
    return _PERFILES[clave]


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
    listo()
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
    listo()
    return ESTADO["zonas"]


@app.get("/api/zonas/{nombre}")
def api_zona(nombre: str):
    listo()
    z = next((z for z in ESTADO["zonas"]["localidades"] if z["nombre"] == nombre), None)
    if z is None:
        log_api.warning("Zona pedida que no existe: %r", nombre)
        raise HTTPException(404, "Localidad desconocida.")
    df = ESTADO["df"]
    g = df[df["localidad"] == nombre]
    log_api.debug("Zona %s: %d avisos", nombre, len(g))
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
               pagina: int = Query(1, ge=1), por_pagina: int = Query(25, ge=5, le=100),
               incluir_retirados: bool = False):
    listo()
    df = ESTADO["df"]
    if not incluir_retirados and TRAZA["por_url"]:
        # Un aviso que el portal ya retiró no se muestra como vigente, aunque siga en el corte del modelo.
        retirados = {u for u, t in TRAZA["por_url"].items() if not t["disponible"]}
        if retirados:
            df = df[~df["url_origen"].isin(retirados)]
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
    log_api.debug("Avisos: filtros localidad=%s barrio=%s comparacion=%s estrato=%s precio_max=%s area_min=%s "
                  "orden=%s -> %d resultados, página %d con %d", localidad, barrio, comparacion, estrato,
                  precio_max, area_min, orden, total, pagina, len(pag))
    if pagina > 1 and len(pag) == 0:
        log_api.info("Página %d de avisos vacía (hay %d resultados)", pagina, total)
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
    return {**ORQUESTADOR.estado(), "puede_ejecutar": es_local(request), "dataset": info_dataset(),
            "verificacion": TRAZA["resumen"]}


@app.post("/api/automatizacion/ejecutar")
def api_automatizacion_ejecutar(request: Request):
    """Solo desde el mismo computador (o con VIVIENDA_TOKEN_ADMIN): nadie de afuera dispara visitas al portal."""
    if not es_local(request):
        log_api.warning("Intento de iniciar un ciclo desde fuera del computador: %s",
                        request.client.host if request.client else "?")
        raise HTTPException(403, "Solo se puede iniciar un ciclo desde el computador donde corre la aplicación.")
    ok, mensaje = ORQUESTADOR.ejecutar_ahora()
    log_api.info("Pedido manual de ciclo: %s (%s)", "aceptado" if ok else "rechazado", mensaje)
    if not ok:
        raise HTTPException(409, mensaje)
    return {"mensaje": mensaje}


# ---------------------------------------------------------------- registros (logs)

class RegistroCliente(BaseModel):
    nivel: Literal["debug", "info", "warn", "error"] = "info"
    mensaje: str = Field(max_length=2000)
    detalle: str | None = Field(default=None, max_length=6000)
    pagina: str | None = Field(default=None, max_length=500)
    navegador: str | None = Field(default=None, max_length=400)
    sesion: str | None = Field(default=None, max_length=40)


_CUPO = {"ventana": 0.0, "n": 0}
NIVELES_NAV = {"debug": logging.DEBUG, "info": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR}


@app.post("/api/registro-cliente", include_in_schema=False, status_code=204)
def api_registro_cliente(lotes: list[RegistroCliente]):
    """El navegador manda aquí sus errores y eventos: así quedan en el mismo archivo que los del servidor.
    Con cupo de 300 mensajes por minuto para que una página en bucle no llene el disco."""
    ahora = time.time()
    if ahora - _CUPO["ventana"] > 60:
        if _CUPO["n"] > 300:
            log_nav.warning("Se descartaron %d mensajes del navegador en el último minuto (cupo)", _CUPO["n"] - 300)
        _CUPO.update(ventana=ahora, n=0)
    for r in lotes[:50]:
        _CUPO["n"] += 1
        if _CUPO["n"] > 300:
            continue
        texto = r.mensaje.replace("\n", " | ")
        extra = f" | detalle: {r.detalle}" if r.detalle else ""
        nav = f" | {r.navegador}" if r.navegador else ""
        log_nav.log(NIVELES_NAV[r.nivel], "[sesión %s] %s%s | página %s%s", r.sesion or "?", texto, extra,
                    r.pagina or "-", nav)
    return None


@app.get("/api/registros", include_in_schema=False, response_class=PlainTextResponse)
def api_registros(request: Request, n: int = Query(400, ge=10, le=5000), errores: bool = False,
                  recolector: bool = False):
    """Últimas líneas del registro, para mirarlas en el navegador. Solo desde el mismo computador.
    ?recolector=1 muestra el del recolector en segundo plano."""
    if not es_local(request):
        raise HTTPException(403, "Los registros solo se pueden ver desde el computador donde corre la aplicación.")
    if recolector:
        ruta = registro.CARPETA / ("recolector-errores.log" if errores else "recolector.log")
        lineas = registro.ultimas_lineas(n, ruta=ruta)
        return f"# {ruta} — últimas {len(lineas)} líneas\n\n" + ("\n".join(lineas) or "(vacío)")
    lineas = registro.ultimas_lineas(n, solo_errores=errores)
    cabecera = (f"# {registro.ERRORES if errores else registro.GENERAL} — últimas {len(lineas)} líneas\n"
                f"# Más detalle: cierre la app y ábrala con  python -m app --depurar\n\n")
    return cabecera + ("\n".join(lineas) if lineas else "(todavía no hay nada registrado)")


@app.get("/api/diagnostico", include_in_schema=False)
def api_diagnostico(request: Request, texto: bool = False):
    """Las mismas revisiones de `python -m app.diagnostico` (JSON, o texto con ?texto=1).
    Solo desde el mismo computador."""
    if not es_local(request):
        raise HTTPException(403, "El diagnóstico solo se puede ver desde el computador donde corre la aplicación.")
    from . import diagnostico
    r = diagnostico.revisar(con_red=False)
    log.info("Diagnóstico pedido desde la página: %s", r["resumen"])
    return PlainTextResponse(diagnostico.texto(r)) if texto else r


# ---------------------------------------------------------------- modelo

@app.get("/api/modelo")
def api_modelo():
    listo()
    return ESTADO["metricas"]


# ---------------------------------------------------------------- interfaz

class Estaticos(StaticFiles):
    """Leaflet y las fuentes se guardan un día en el navegador. Los archivos propios (app.js,
    estilos, mapas) se revalidan en cada carga: el navegador pregunta y el servidor contesta 304
    si no cambiaron. Así, después de actualizar la aplicación nadie se queda con el app.js viejo."""

    async def get_response(self, path, scope):
        r = await super().get_response(path, scope)
        if r.status_code in (200, 304):
            r.headers["Cache-Control"] = "public, max-age=86400" if path.startswith("vendor/") else "no-cache"
        return r


app.mount("/static", Estaticos(directory=ESTATICOS), name="static")


VERSIONADOS = ("registro.js", "app.js", "estilos.css", "navegador-antiguo.js")
_PAGINA: dict = {}


def pagina_versionada() -> str:
    """index.html con ?v=<huella> en app.js, estilos y registro: cada versión nueva de esos archivos
    tiene otra dirección, así ningún navegador sigue usando una copia vieja guardada en caché
    (pasó en Windows: el app.js anterior seguía activo una hora después de actualizar)."""
    import hashlib
    rutas = [ESTATICOS / "index.html", *(ESTATICOS / n for n in VERSIONADOS)]
    clave = tuple(r.stat().st_mtime_ns for r in rutas)
    if _PAGINA.get("clave") != clave:
        html = rutas[0].read_text(encoding="utf-8")
        for n in VERSIONADOS:
            v = hashlib.sha256((ESTATICOS / n).read_bytes()).hexdigest()[:10]
            html = html.replace(f'"/static/{n}"', f'"/static/{n}?v={v}"')
        _PAGINA.update(clave=clave, html=html)
        log.debug("Página versionada de nuevo: %s", clave)
    return _PAGINA["html"]


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def inicio():
    return HTMLResponse(pagina_versionada(), headers={"Cache-Control": "no-cache"})


@app.exception_handler(KeyError)
async def sin_estado(request: Request, exc: KeyError):
    """Un KeyError aquí suele significar que el arranque no terminó: 503 sin filtrar detalles internos.
    Si el estado sí está cargado, es un error de programa: se registra con la traza completa."""
    if "art" in ESTADO:
        log_api.error("KeyError %s en %s con el estado ya cargado (error de programa)", exc, request.url.path,
                      exc_info=exc)
        return JSONResponse({"detail": "Error interno. Revise datos/registros/errores.log.",
                             "rid": registro.RID.get()}, status_code=500)
    log_api.error("KeyError en %s: %s (el dataset o el modelo todavía no están cargados)", request.url.path, exc)
    return JSONResponse({"detail": "La aplicación todavía no cargó el dataset ni el modelo."}, status_code=503)


@app.exception_handler(RequestValidationError)
async def datos_invalidos(request: Request, exc: RequestValidationError):
    """Datos mal enviados (p. ej. área fuera de rango): se registra qué campo y qué valor llegaron."""
    errores = [{"campo": ".".join(str(p) for p in e.get("loc", [])), "problema": e.get("msg"),
                "valor": str(e.get("input"))[:120]} for e in exc.errors()]
    log_api.warning("Datos inválidos en %s %s: %s", request.method, request.url.path, errores)
    return JSONResponse({"detail": exc.errors() and [
        {"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]}, status_code=422)


@app.exception_handler(StarletteHTTPException)
async def error_http(request: Request, exc: StarletteHTTPException):
    nivel = logging.DEBUG if exc.status_code == 404 and not request.url.path.startswith("/api/") else logging.INFO
    log_api.log(nivel, "%s %s -> %d: %s", request.method, request.url.path, exc.status_code, exc.detail)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.exception_handler(Exception)
async def error_inesperado(request: Request, exc: Exception):
    log_api.error("Error inesperado en %s %s: %s: %s", request.method, request.url.path, type(exc).__name__, exc,
                  exc_info=exc)
    return JSONResponse({"detail": "Error interno. Revise datos/registros/errores.log.", "rid": registro.RID.get()},
                        status_code=500)
