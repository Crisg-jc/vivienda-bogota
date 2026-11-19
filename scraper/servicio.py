"""Recolector en segundo plano: alimenta la base aunque la aplicación (y el navegador) estén cerrados.

    python -m scraper.servicio            # corre hasta que se apague el computador
    pythonw -m scraper.servicio           # igual, sin ventana (así lo deja iniciar.bat en Windows)

Qué hace, en bucle:
  1. Corre un ciclo (scraper/ciclo.py): listado, avisos nuevos, verificación de los guardados,
     dataset y modelo nuevos si hubo cambios.
  2. Decide cuándo seguir: si quedan avisos por leer o falta recorrer el listado, sigue al minuto;
     si la base está al día, espera intervalo_horas; si el portal limitó el acceso, espera 6 h
     (y el doble cada vez, hasta 48 h); sin internet, reintenta en 30 min.
  3. Escribe un latido en datos/estado/servicio.json: la app lo lee para mostrar el estado y para no
     lanzar ciclos propios mientras el recolector esté vivo.

Respeta lo mismo que el ciclo: solo portales con terminos_revisados: true, robots.txt y pausas de
4 a 8 s entre solicitudes. Un solo recolector a la vez (si ya hay uno vivo, este se cierra).
Registro: datos/registros/recolector.log y recolector-errores.log.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import signal
import threading
import time

from comun import registro
from scraper import ciclo as cy

log = logging.getLogger("vivienda.recolector")
LATIDO = cy.CARPETA / "servicio.json"
PEDIDO = cy.CARPETA / "pedido.json"
VIVO_SEGUNDOS = 120


def leer_latido() -> dict:
    try:
        return json.loads(LATIDO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def vivo(latido: dict | None = None) -> bool:
    """¿Hay un recolector corriendo? Su latido se renueva cada 20 s."""
    d = latido if latido is not None else leer_latido()
    try:
        ultimo = dt.datetime.fromisoformat(d["latido"])
    except (KeyError, ValueError, TypeError):
        return False
    return (dt.datetime.now() - ultimo).total_seconds() < VIVO_SEGUNDOS and d.get("estado") != "detenido"


def pedir_ciclo() -> None:
    """La app pide un ciclo ya (botón 'Actualizar ahora'): el recolector lo ve en segundos."""
    cy.CARPETA.mkdir(parents=True, exist_ok=True)
    PEDIDO.write_text(json.dumps({"fecha": dt.datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")


class Recolector:
    def __init__(self):
        self.parar = threading.Event()
        self.estado = {"pid": os.getpid(), "inicio": self.ahora(), "estado": "iniciando", "proximo": None,
                       "ciclos": 0, "ultimo_resultado": None, "pausa_hasta": None}
        self.bloqueos = 0

    @staticmethod
    def ahora() -> str:
        return dt.datetime.now().isoformat(timespec="seconds")

    def latir(self, **cambios):
        self.estado.update(cambios, latido=self.ahora())
        cy.CARPETA.mkdir(parents=True, exist_ok=True)
        tmp = LATIDO.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.estado, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, LATIDO)

    def _latidos(self):
        while not self.parar.wait(20):
            try:
                self.latir()
            except OSError:
                log.exception("No se pudo escribir el latido")

    def esperar(self, segundos: float, motivo: str):
        hasta = dt.datetime.now() + dt.timedelta(seconds=segundos)
        self.latir(estado="esperando", proximo=hasta.isoformat(timespec="seconds"), motivo=motivo)
        log.info("Próximo ciclo a las %s (%s)", hasta.strftime("%H:%M"), motivo)
        while not self.parar.is_set() and dt.datetime.now() < hasta:
            if cy.PARAR.exists():
                log.info("Se pidió detener el recolector (archivo %s)", cy.PARAR)
                self.parar.set()
                return
            if PEDIDO.exists():
                PEDIDO.unlink(missing_ok=True)
                log.info("La aplicación pidió un ciclo ahora")
                return
            self.parar.wait(5)

    def hay_trabajo(self, conf: dict) -> bool:
        """Quedan avisos por leer o falta terminar de recorrer el listado."""
        from sqlalchemy import func, select

        from scraper import db
        with db.motor().begin() as c:
            pendientes = c.scalar(select(func.count()).where(db.enlaces.c.procesado.is_(False))) or 0
        cursores = cy.leer_estado().get("cursor") or {}
        listado_a_medias = any(int(cursores.get(k, 0)) > 0 for k, p in conf["portales"].items()
                               if p.get("habilitado") and p.get("terminos_revisados"))
        log.info("Trabajo pendiente: %d avisos por leer; listado %s", pendientes,
                 "a medio recorrer" if listado_a_medias else "recorrido completo")
        return pendientes > 0 or listado_a_medias

    def bucle(self):
        threading.Thread(target=self._latidos, daemon=True, name="latido").start()
        while not self.parar.is_set():
            try:
                conf = cy.cargar_conf()
                aj = cy.ajustes(conf)
            except Exception:
                log.exception("No se pudo leer config/portales.yaml; se reintenta en 1 minuto")
                self.esperar(60, "configuración ilegible")
                continue
            if not aj["activo"]:
                self.esperar(600, "automatico.activo es false")
                continue
            if not any(p.get("habilitado") and p.get("terminos_revisados") for p in conf["portales"].values()):
                self.esperar(60, "ningún portal autorizado (terminos_revisados)")
                continue
            self.latir(estado="en_curso", proximo=None, motivo="")
            t0 = time.perf_counter()
            try:
                r = cy.ejecutar(conf)
            except Exception:
                log.exception("El ciclo falló de forma inesperada")
                r = {"resultado": "error"}
            self.estado["ciclos"] += 1
            res = r.get("resultado")
            self.latir(ultimo_resultado=res)
            log.info("Ciclo %d terminado en %.0f min: %s", self.estado["ciclos"], (time.perf_counter() - t0) / 60,
                     {k: v for k, v in r.items() if k not in ("mensaje",)})
            if res == "bloqueado":
                horas = min(48, aj["espera_tras_bloqueo_horas"] * 2 ** self.bloqueos)
                self.bloqueos += 1
                self.latir(pausa_hasta=(dt.datetime.now() + dt.timedelta(hours=horas)).isoformat(timespec="seconds"))
                self.esperar(horas * 3600, f"el portal limitó el acceso; pausa de {horas} h")
                continue
            self.bloqueos = 0
            self.latir(pausa_hasta=None)
            if res in ("sin_conexion", "error"):
                self.esperar(1800, "sin conexión o error; se reintenta")
            elif res == "en_curso":
                self.esperar(120, "otro ciclo en curso")
            elif self.hay_trabajo(conf):
                self.esperar(60, "quedan avisos por leer")
            else:
                self.esperar(aj["intervalo_horas"] * 3600, "base al día")
        self.latir(estado="detenido", proximo=None)
        log.info("==== Recolector detenido ====")


def detener_recolector() -> None:
    """Pide al recolector que termine (lo usa quitar-recolector.bat): acaba el aviso en curso y sale."""
    cy.CARPETA.mkdir(parents=True, exist_ok=True)
    cy.PARAR.write_text(dt.datetime.now().isoformat(timespec="seconds"), encoding="utf-8")


def main():
    import sys
    registro.configurar("recolector", archivo="recolector")
    if "--detener" in sys.argv:
        detener_recolector()
        log.info("Se pidió detener el recolector")
        print("Se pidió detener el recolector: termina en unos segundos (o al acabar el aviso que lee).")
        return
    cy.PARAR.unlink(missing_ok=True)
    if vivo():
        log.info("Ya hay un recolector vivo (pid %s): este se cierra", leer_latido().get("pid"))
        return
    r = Recolector()

    def detener(*_):
        log.info("Señal de cierre recibida")
        r.parar.set()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, detener)
        except (ValueError, OSError):
            pass
    log.info("==== Recolector en segundo plano iniciado (pid %s) ====", os.getpid())
    r.latir(estado="iniciando")
    try:
        r.bucle()
    except KeyboardInterrupt:
        r.latir(estado="detenido")


if __name__ == "__main__":
    main()
