"""Registro (logs) de toda la aplicación, en un solo lugar.

Dónde quedan:
  datos/registros/vivienda.log   todo lo que pasa (rota a los 5 MB, guarda 5 copias)
  datos/registros/errores.log    solo avisos y errores, para ir directo al problema
  datos/estado/ciclo.log         la salida del ciclo automático de recolección (otro proceso)
  la consola                     lo mismo que vivienda.log, mientras la app está abierta

Nivel de detalle: variable VIVIENDA_LOG (DEBUG, INFO, WARNING) o `python -m app --depurar`.
En DEBUG se registra cada paso (cada solicitud HTTP al portal, cada filtro de limpieza, cada
llamada a la API con sus parámetros). En INFO, los pasos importantes y todos los errores.

Cada línea lleva: fecha y hora con milisegundos, nivel, proceso, módulo, identificador de la
solicitud web (rid=...) cuando la hay, el mensaje y el archivo:línea que la escribió.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import logging.handlers
import os
import pathlib
import platform
import sys
import threading
import time

RAIZ = pathlib.Path(__file__).resolve().parents[1]
CARPETA = pathlib.Path(os.environ.get("VIVIENDA_REGISTROS", RAIZ / "datos" / "registros"))
GENERAL = CARPETA / "vivienda.log"
ERRORES = CARPETA / "errores.log"

# Identificador de la solicitud web en curso; el middleware lo fija y cada línea lo muestra.
RID: contextvars.ContextVar[str] = contextvars.ContextVar("rid", default="-")

FORMATO = ("%(asctime)s.%(msecs)03d %(levelname)-7s [%(proceso)s] %(name)s rid=%(rid)s | "
           "%(message)s  (%(filename)s:%(lineno)d %(funcName)s)")
FECHA = "%Y-%m-%d %H:%M:%S"
_configurado = False


class _Contexto(logging.Filter):
    def __init__(self, proceso: str):
        super().__init__()
        self.proceso = proceso

    def filter(self, r: logging.LogRecord) -> bool:
        r.rid = RID.get()
        r.proceso = self.proceso
        return True


def nivel_pedido(predeterminado: str = "INFO") -> int:
    nombre = os.environ.get("VIVIENDA_LOG", predeterminado).upper()
    return getattr(logging, nombre, logging.INFO)


def configurar(proceso: str = "app", nivel: int | str | None = None, archivo: bool = True,
               consola: bool = True) -> logging.Logger:
    """Deja listo el registro para este proceso. Se puede llamar varias veces: solo actúa la primera."""
    global _configurado
    raiz = logging.getLogger()
    if _configurado:
        if nivel is not None:
            raiz.setLevel(nivel)
        return logging.getLogger("vivienda")
    nivel = nivel_pedido() if nivel is None else nivel
    if isinstance(nivel, str):
        nivel = getattr(logging, nivel.upper(), logging.INFO)
    formato = logging.Formatter(FORMATO, FECHA)
    contexto = _Contexto(proceso)

    for h in list(raiz.handlers):          # basicConfig previo, pytest, etc.
        if getattr(h, "_vivienda", False):
            raiz.removeHandler(h)
    # En Windows, con la salida redirigida a un archivo, la consola usa cp1252: una tilde o un símbolo
    # raro no debe tumbar el registro, se reemplaza.
    with contextlib.suppress(AttributeError, ValueError):
        sys.stderr.reconfigure(errors="backslashreplace")
        sys.stdout.reconfigure(errors="backslashreplace")
    if consola:
        h_consola = logging.StreamHandler(sys.stderr)
        _preparar(h_consola, formato, contexto)
        raiz.addHandler(h_consola)

    if archivo:
        try:
            CARPETA.mkdir(parents=True, exist_ok=True)
            # delay=True: el archivo se abre al primer mensaje; encoding fijo para que Windows no use cp1252.
            general = logging.handlers.RotatingFileHandler(GENERAL, maxBytes=5_000_000, backupCount=5,
                                                           encoding="utf-8", delay=True)
            _preparar(general, formato, contexto)
            errores = logging.handlers.RotatingFileHandler(ERRORES, maxBytes=2_000_000, backupCount=3,
                                                           encoding="utf-8", delay=True)
            errores.setLevel(logging.WARNING)
            _preparar(errores, formato, contexto, nivel_fijo=True)
            raiz.addHandler(general)
            raiz.addHandler(errores)
        except OSError as e:               # carpeta sin permiso de escritura: al menos queda la consola
            raiz.warning("No se pudo crear el archivo de registro en %s: %s", CARPETA, e)

    raiz.setLevel(nivel)
    # Librerías muy conversadoras: se dejan en WARNING salvo que se pida DEBUG explícito para ellas.
    for ruidoso in ("urllib3", "multipart", "matplotlib", "PIL", "asyncio", "watchfiles", "httpx", "httpcore"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)   # la app ya escribe una línea por solicitud
    logging.captureWarnings(True)          # warnings de pandas/sklearn también quedan en el registro

    _ganchos_de_excepciones()
    _configurado = True
    log = logging.getLogger("vivienda")
    log.debug("Registro configurado: proceso=%s nivel=%s archivo=%s", proceso, logging.getLevelName(nivel),
              GENERAL if archivo else "(solo consola)")
    return log


def _preparar(h: logging.Handler, formato, contexto, nivel_fijo: bool = False):
    h.setFormatter(formato)
    h.addFilter(contexto)
    h._vivienda = True  # type: ignore[attr-defined]
    if not nivel_fijo:
        h.setLevel(logging.DEBUG)


def _ganchos_de_excepciones():
    """Ninguna excepción se pierde: las no atrapadas del programa y de los hilos van al registro."""
    log = logging.getLogger("vivienda.excepciones")

    def principal(tipo, valor, tb):
        if issubclass(tipo, KeyboardInterrupt):
            log.info("Detenido con Ctrl+C")
            return
        log.critical("Excepción no atrapada: %s: %s", tipo.__name__, valor, exc_info=(tipo, valor, tb))

    def de_hilo(a: threading.ExceptHookArgs):
        log.critical("Excepción no atrapada en el hilo %s: %s", a.thread.name if a.thread else "?",
                     a.exc_value, exc_info=(a.exc_type, a.exc_value, a.exc_traceback))

    sys.excepthook = principal
    threading.excepthook = de_hilo


def entorno() -> dict:
    """Datos del equipo y de las librerías: lo primero que se mira cuando algo falla en otro PC."""
    from importlib import metadata
    paquetes = {}
    for p in ("fastapi", "uvicorn", "pandas", "numpy", "scikit-learn", "SQLAlchemy", "requests",
              "beautifulsoup4", "lxml", "PyYAML", "joblib", "pydantic"):
        try:
            paquetes[p] = metadata.version(p)
        except metadata.PackageNotFoundError:
            paquetes[p] = "NO INSTALADO"
    return {"python": sys.version.split()[0], "ejecutable": sys.executable,
            "sistema": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "carpeta": str(RAIZ), "directorio_actual": os.getcwd(), "pid": os.getpid(),
            "codificacion": sys.getfilesystemencoding(), "paquetes": paquetes}


@contextlib.contextmanager
def cronometro(log: logging.Logger, que: str, nivel: int = logging.INFO, **detalles):
    """Registra el inicio (DEBUG), el fin con la duración y, si falla, el error con su traza.

        with cronometro(log, "Entrenar modelo", filas=1200):
            ...
    """
    extra = " ".join(f"{k}={v}" for k, v in detalles.items())
    log.debug("INICIO %s %s", que, extra, stacklevel=3)   # 3: muestra quién llamó, no este archivo
    t = time.perf_counter()
    try:
        yield
    except Exception:
        log.exception("FALLO %s tras %.2fs %s", que, time.perf_counter() - t, extra, stacklevel=3)
        raise
    log.log(nivel, "LISTO %s en %.2fs %s", que, time.perf_counter() - t, extra, stacklevel=3)


def ultimas_lineas(n: int = 300, solo_errores: bool = False) -> list[str]:
    """Las últimas n líneas del registro, sin cargar el archivo entero en memoria."""
    ruta = ERRORES if solo_errores else GENERAL
    if not ruta.exists():
        return []
    with open(ruta, "rb") as f:
        f.seek(0, os.SEEK_END)
        tam, bloque, datos = f.tell(), 65536, b""
        while tam > 0 and datos.count(b"\n") <= n:
            leer = min(bloque, tam)
            tam -= leer
            f.seek(tam)
            datos = f.read(leer) + datos
    return datos.decode("utf-8", "replace").splitlines()[-n:]
