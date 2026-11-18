"""Arranque rápido:  python -m app  [--host 0.0.0.0] [--port 8000] [--depurar] [--abrir]

--depurar  registra cada paso con todo detalle (igual que VIVIENDA_LOG=DEBUG)
--abrir    abre el navegador cuando la aplicación está lista
"""
import argparse
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

ap = argparse.ArgumentParser(description="Aplicación de estimación de precios de apartamentos en Bogotá")
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--port", type=int, default=8000)
ap.add_argument("--reload", action="store_true")
ap.add_argument("--depurar", action="store_true", help="registro con máximo detalle (DEBUG)")
ap.add_argument("--abrir", action="store_true", help="abrir el navegador al terminar de cargar")
a = ap.parse_args()
if a.depurar:
    os.environ["VIVIENDA_LOG"] = "DEBUG"     # también lo heredan el modo --reload y el ciclo automático

from comun import registro  # noqa: E402  (después de fijar VIVIENDA_LOG)

log = registro.configurar("app")
datos = registro.entorno()
log.info("==== Iniciando la aplicación ====")
log.info("Python %s en %s | carpeta %s | pid %s", datos["python"], datos["sistema"], datos["carpeta"], datos["pid"])
log.info("Ejecutable: %s | directorio actual: %s", datos["ejecutable"], datos["directorio_actual"])
log.info("Paquetes: %s", ", ".join(f"{k} {v}" for k, v in datos["paquetes"].items()))
faltan = [k for k, v in datos["paquetes"].items() if v == "NO INSTALADO"]
if faltan:
    log.error("Faltan paquetes: %s. Ejecute:  .venv\\Scripts\\python.exe -m pip install -r requirements.txt",
              ", ".join(faltan))
if sys.version_info < (3, 11):
    log.error("Se necesita Python 3.11 o más reciente; este es %s.", datos["python"])
log.info("Registro en %s (nivel %s)", registro.GENERAL, logging.getLevelName(logging.getLogger().level))

# ¿Está ocupado el puerto? Pasa cuando la app ya está abierta en otra ventana.
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.settimeout(0.5)
    if s.connect_ex(("127.0.0.1" if a.host == "0.0.0.0" else a.host, a.port)) == 0:
        log.error("El puerto %s ya está en uso: probablemente la aplicación ya está abierta en otra ventana. "
                  "Ciérrela con Ctrl+C o use --port 8001.", a.port)
        sys.exit(1)

direccion = f"http://{'127.0.0.1' if a.host == '0.0.0.0' else a.host}:{a.port}"
log.info("Abra %s en el navegador (Ctrl+C para cerrar).", direccion)


def abrir_cuando_este_lista():
    for _ in range(240):                       # hasta 2 minutos: el primer arranque puede entrenar el modelo
        try:
            with urllib.request.urlopen(direccion + "/api/salud", timeout=2) as r:
                if r.status == 200:
                    log.info("La aplicación respondió; abriendo el navegador.")
                    webbrowser.open(direccion)
                    return
        except Exception:
            time.sleep(0.5)
    log.warning("La aplicación no respondió en 2 minutos; no se abrió el navegador. Revise los errores arriba.")


if a.abrir:
    threading.Thread(target=abrir_cuando_este_lista, daemon=True, name="abrir-navegador").start()

import uvicorn  # noqa: E402

try:
    # log_config=None: uvicorn usa el mismo registro que la app (mismo formato y mismos archivos).
    # access_log=False: la app ya escribe una línea por solicitud, con su duración y su rid.
    uvicorn.run("app.main:app", host=a.host, port=a.port, reload=a.reload, log_config=None, access_log=False,
                log_level="debug" if a.depurar else "info")
except KeyboardInterrupt:
    pass
finally:
    log.info("==== Aplicación cerrada ====")
