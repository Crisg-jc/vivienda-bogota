"""Orquestador: lanza el ciclo automático al abrir la aplicación y lo repite cada cierto tiempo.

Cada ciclo corre en un proceso aparte (python -m scraper.ciclo): si algo falla allí, la aplicación
sigue respondiendo. Mientras tanto, este hilo vigila el dataset y el modelo; cuando el ciclo publica
un corte nuevo, la aplicación lo carga sin reiniciarse.

Se apaga con `automatico.activo: false` en config/portales.yaml o con la variable VIVIENDA_AUTOMATICO=0.
"""
import datetime as dt
import logging
import os
import subprocess
import sys
import threading

from sqlalchemy import func, or_, select

from scraper import ciclo as cy
from scraper import db
from scraper.recolectar import RAIZ, cargar_conf

log = logging.getLogger("vivienda.automatico")
REGISTRO = cy.CARPETA / "ciclo.log"


def ahora() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


class Orquestador:
    def __init__(self, recargar):
        self.recargar = recargar            # función de la app: recarga datos y modelo si cambiaron
        self.parar = threading.Event()
        self.despertar = threading.Event()
        self.proceso: subprocess.Popen | None = None
        self.proximo: dt.datetime | None = None
        self.pausa_hasta: dt.datetime | None = None
        self.bloqueos_seguidos = 0
        self.hilo: threading.Thread | None = None
        self._motor = None

    # ---------------------------------------------------------------- configuración
    @staticmethod
    def ajustes() -> tuple[dict, dict]:
        conf = cargar_conf()
        return conf, cy.ajustes(conf)

    def activo(self) -> bool:
        if os.environ.get("VIVIENDA_AUTOMATICO", "1") == "0":
            return False
        return bool(self.ajustes()[1]["activo"])

    def motor(self):
        if self._motor is None:
            self._motor = db.motor()
        return self._motor

    # ---------------------------------------------------------------- ciclo de vida
    def iniciar(self):
        if not self.activo():
            log.info("Automatización apagada (config o VIVIENDA_AUTOMATICO=0).")
            return
        _, aj = self.ajustes()
        self.proximo = ahora() + dt.timedelta(seconds=aj["primer_ciclo_tras_segundos"])
        self.hilo = threading.Thread(target=self._bucle, name="ciclo-automatico", daemon=True)
        self.hilo.start()
        log.info("Automatización activa: primer ciclo a las %s, luego cada %s h.",
                 self.proximo.strftime("%H:%M:%S"), aj["intervalo_horas"])

    def detener(self):
        self.parar.set()
        self.despertar.set()
        if self.proceso and self.proceso.poll() is None:
            self.proceso.terminate()
            try:
                self.proceso.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proceso.kill()
            cy.soltar_candado()          # el ciclo cortado no alcanzó a soltarlo
            cy.escribir_estado(estado="inactivo", fase="", detalle="")

    def ejecutar_ahora(self) -> tuple[bool, str]:
        if self.hilo is None:
            return False, "La automatización está apagada."
        if self.proceso and self.proceso.poll() is None:
            return False, "Ya hay un ciclo en curso."
        if self.pausa_hasta and ahora() < self.pausa_hasta:
            return False, f"En pausa por un bloqueo del portal hasta las {self.pausa_hasta:%H:%M}."
        self.proximo = ahora()
        self.despertar.set()
        return True, "Ciclo iniciado."

    def _bucle(self):
        while not self.parar.is_set():
            try:
                if self.proximo and ahora() >= self.proximo and not (self.pausa_hasta and ahora() < self.pausa_hasta):
                    self._correr_ciclo()
                self.recargar()
            except Exception:  # el hilo nunca debe morir en silencio
                log.exception("Error en el orquestador")
            self.despertar.wait(timeout=20)
            self.despertar.clear()

    def _correr_ciclo(self):
        conf, aj = self.ajustes()
        if not aj["activo"]:
            self.proximo = ahora() + dt.timedelta(minutes=10)
            return
        cy.CARPETA.mkdir(parents=True, exist_ok=True)
        if REGISTRO.exists() and REGISTRO.stat().st_size > 5_000_000:
            REGISTRO.replace(REGISTRO.with_suffix(".log.1"))
        log.info("Iniciando ciclo automático de recolección")
        with open(REGISTRO, "a", encoding="utf-8") as salida:
            salida.write(f"\n===== {ahora().isoformat()} =====\n")
            salida.flush()
            self.proceso = subprocess.Popen([sys.executable, "-m", "scraper.ciclo"], cwd=RAIZ,
                                            stdout=salida, stderr=subprocess.STDOUT)
            while self.proceso.poll() is None and not self.parar.is_set():
                self.parar.wait(timeout=15)
                self.recargar()          # los avances se ven en la interfaz mientras el ciclo sigue
        if self.parar.is_set():
            return
        res = cy.leer_estado().get("ultimo_ciclo") or {}
        if res.get("resultado") == "bloqueado":
            horas = min(48, aj["espera_tras_bloqueo_horas"] * 2 ** self.bloqueos_seguidos)
            self.bloqueos_seguidos += 1
            self.pausa_hasta = ahora() + dt.timedelta(hours=horas)
            self.proximo = self.pausa_hasta
            log.warning("El portal limitó el acceso: siguiente intento en %s h.", horas)
        elif res.get("resultado") in ("sin_conexion", "error"):
            self.pausa_hasta = None       # falla pasajera (internet, portal caído): se reintenta pronto
            self.proximo = ahora() + dt.timedelta(hours=1)
        else:
            self.bloqueos_seguidos = 0
            self.pausa_hasta = None
            self.proximo = ahora() + dt.timedelta(hours=aj["intervalo_horas"])
        log.info("Ciclo terminado (%s). Próximo: %s", res.get("resultado"), self.proximo)

    # ---------------------------------------------------------------- estado para la interfaz
    def estado(self) -> dict:
        conf, aj = self.ajustes()
        ciclo = cy.leer_estado()
        en_curso = bool(self.proceso and self.proceso.poll() is None)
        autorizados = [k for k, p in conf["portales"].items() if p.get("habilitado") and p.get("terminos_revisados")]
        if self.hilo is None:
            situacion = "apagado"
        elif en_curso:
            situacion = "en_curso"
        elif self.pausa_hasta and ahora() < self.pausa_hasta:
            situacion = "pausado"
        elif not autorizados:
            situacion = "sin_portales"
        else:
            situacion = "esperando"
        return {
            "situacion": situacion,
            "fase": ciclo.get("fase") if en_curso else "",
            "detalle": ciclo.get("detalle") if en_curso else "",
            "contadores": ciclo.get("contadores") if en_curso else None,
            "ultimo_ciclo": ciclo.get("ultimo_ciclo"),
            "proximo_ciclo": self.proximo.isoformat() if self.proximo and not en_curso else None,
            "pausa_hasta": self.pausa_hasta.isoformat() if self.pausa_hasta else None,
            "intervalo_horas": aj["intervalo_horas"],
            "dias_entre_revisiones": aj["dias_entre_revisiones"],
            "portales_autorizados": autorizados,
            "base": self.resumen_base(),
            "ejecuciones": self.ejecuciones(),
        }

    def resumen_base(self) -> dict:
        try:
            eng = self.motor()
        except Exception:
            return {}
        e, t = db.enlaces.c, db.inmuebles_crudos.c
        semana = ahora() - dt.timedelta(days=7)
        with eng.begin() as c:
            cuenta = lambda q: c.scalar(q) or 0  # noqa: E731
            return {
                "enlaces": cuenta(select(func.count()).select_from(db.enlaces)),
                "pendientes": cuenta(select(func.count()).where(e.procesado.is_(False))),
                "guardados": cuenta(select(func.count()).select_from(db.inmuebles_crudos)),
                "disponibles": cuenta(select(func.count()).where(or_(t.disponible.is_(None), t.disponible.is_(True)))),
                "retirados": cuenta(select(func.count()).where(t.disponible.is_(False))),
                "cambios_precio_7_dias": cuenta(select(func.count()).where(
                    db.cambios.c.campo == "precio", db.cambios.c.fecha >= semana)),
            }

    def ejecuciones(self, n: int = 8) -> list[dict]:
        try:
            with self.motor().begin() as c:
                filas = c.execute(select(db.ejecuciones).order_by(db.ejecuciones.c.id.desc()).limit(n)).mappings()
                return [{k: (v.isoformat() if isinstance(v, dt.datetime) else v) for k, v in f.items()} for f in filas]
        except Exception:
            return []
