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
from scraper import db, servicio
from scraper.recolectar import RAIZ, cargar_conf

log = logging.getLogger("vivienda.automatico")
log_ciclo = logging.getLogger("vivienda.ciclo")
REGISTRO = cy.CARPETA / "ciclo.log"


def ahora() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


def con_zona(v):
    """Fecha en ISO 8601 con zona horaria (2026-11-17T21:40:00-05:00).

    Sin la zona, algunos navegadores (Safari antiguo) la leen como hora UTC y la muestran corrida."""
    if v is None:
        return None
    if isinstance(v, str):
        try:
            v = dt.datetime.fromisoformat(v)
        except ValueError:
            return v
    return (v if v.tzinfo else v.astimezone()).isoformat(timespec="seconds")


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
            log.debug("VIVIENDA_AUTOMATICO=0: automatización apagada por variable de entorno")
            return False
        try:
            aj = self.ajustes()[1]
        except Exception:
            log.exception("No se pudo leer config/portales.yaml: la automatización queda apagada. "
                          "Revise que el archivo exista y que la sangría (espacios) sea correcta.")
            return False
        log.debug("Ajustes de automatización: %s", aj)
        return bool(aj["activo"])

    def motor(self):
        if self._motor is None:
            self._motor = db.motor()
        return self._motor

    # ---------------------------------------------------------------- ciclo de vida
    # ---------------------------------------------------------------- recolector en segundo plano
    def segundo_plano(self) -> bool:
        try:
            return bool(self.ajustes()[1].get("segundo_plano", True))
        except Exception:
            return False

    def lanzar_recolector(self) -> bool:
        """Arranca scraper.servicio como proceso independiente: sigue vivo aunque se cierre la app."""
        if servicio.vivo():
            return True
        exe = sys.executable
        try:
            if os.name == "nt":
                w = os.path.join(os.path.dirname(exe), "pythonw.exe")
                exe = w if os.path.exists(w) else exe
                # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: sin ventana y sin recibir el Ctrl+C de la app
                p = subprocess.Popen([exe, "-m", "scraper.servicio"], cwd=RAIZ, creationflags=0x00000008 | 0x00000200,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     close_fds=True)
            else:
                p = subprocess.Popen([exe, "-m", "scraper.servicio"], cwd=RAIZ, start_new_session=True,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            log.exception("No se pudo arrancar el recolector en segundo plano; la app recolecta mientras esté abierta")
            return False
        log.info("Recolector en segundo plano arrancado (pid %s, %s). Sigue trabajando aunque cierre la app; "
                 "registro en datos/registros/recolector.log", p.pid, exe)
        return True

    def iniciar(self):
        if not self.activo():
            log.info("Automatización apagada (config o VIVIENDA_AUTOMATICO=0).")
            return
        conf, aj = self.ajustes()
        for nombre, p in conf["portales"].items():
            if not p.get("habilitado"):
                log.info("Portal %s: deshabilitado en config/portales.yaml", nombre)
            elif not p.get("terminos_revisados"):
                log.warning("Portal %s: falta poner terminos_revisados: true en config/portales.yaml "
                            "(después de leer sus términos). Mientras tanto no se recolecta nada de él.", nombre)
            else:
                quien = str(p.get("revisado_por") or "")
                if not quien or "NOMBRE" in quien.upper():
                    log.warning("Portal %s: autorizado, pero revisado_por no tiene un nombre real (%r). "
                                "Ponga el nombre de quien leyó los términos.", nombre, quien)
                else:
                    log.info("Portal %s: autorizado (revisó %s)", nombre, quien)
        contacto = str(conf.get("general", {}).get("user_agent", ""))
        if "cambiar" in contacto.lower() or "example" in contacto.lower() or "@" not in contacto:
            log.warning("El user_agent de config/portales.yaml no tiene un correo de contacto real: %r", contacto)
        self.proximo = ahora() + dt.timedelta(seconds=aj["primer_ciclo_tras_segundos"])
        if self.segundo_plano():
            if servicio.vivo():
                log.info("Ya hay un recolector en segundo plano trabajando (pid %s): la app solo muestra su estado",
                         servicio.leer_latido().get("pid"))
            else:
                self.lanzar_recolector()
        self.hilo = threading.Thread(target=self._bucle, name="ciclo-automatico", daemon=True)
        self.hilo.start()
        log.info("Automatización activa: primer ciclo a las %s, luego cada %s h.",
                 self.proximo.strftime("%H:%M:%S"), aj["intervalo_horas"])

    def detener(self):
        self.parar.set()
        self.despertar.set()
        if servicio.vivo():
            log.info("La app se cierra; el recolector en segundo plano sigue alimentando la base")
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
        if self.segundo_plano() and servicio.vivo():
            if cy.leer_estado().get("estado") == "en_curso":
                return False, "Ya hay un ciclo en curso."
            servicio.pedir_ciclo()
            return True, "Ciclo pedido al recolector en segundo plano."
        if self.proceso and self.proceso.poll() is None:
            return False, "Ya hay un ciclo en curso."
        if self.pausa_hasta and ahora() < self.pausa_hasta:
            return False, f"En pausa por un bloqueo del portal hasta las {self.pausa_hasta:%H:%M}."
        self.proximo = ahora()
        self.despertar.set()
        return True, "Ciclo iniciado."

    def _bucle(self):
        log.debug("Hilo del orquestador iniciado")
        while not self.parar.is_set():
            try:
                if self.segundo_plano():
                    # El recolector hace el trabajo; si murió (se cerró la sesión, un error), se vuelve a lanzar.
                    if not servicio.vivo() and self.proximo and ahora() >= self.proximo:
                        log.warning("El recolector en segundo plano no responde: se arranca de nuevo")
                        self.lanzar_recolector()
                        self.proximo = ahora() + dt.timedelta(minutes=3)
                elif self.proximo and ahora() >= self.proximo and not (self.pausa_hasta and ahora() < self.pausa_hasta):
                    self._correr_ciclo()
                else:
                    log.debug("Esperando: próximo ciclo %s, pausa hasta %s", self.proximo, self.pausa_hasta)
                self.recargar()
            except Exception:  # el hilo nunca debe morir en silencio
                log.exception("Error en el orquestador")
            self.despertar.wait(timeout=20)
            self.despertar.clear()
        log.debug("Hilo del orquestador terminado")

    def _correr_ciclo(self):
        conf, aj = self.ajustes()
        if not aj["activo"]:
            log.info("automatico.activo es false: no se lanza el ciclo; se vuelve a mirar en 10 minutos")
            self.proximo = ahora() + dt.timedelta(minutes=10)
            return
        # Sin portales autorizados no se lanza nada; se vuelve a mirar la configuración cada minuto,
        # así el ciclo arranca solo apenas alguien pone terminos_revisados: true (sin reiniciar).
        if not any(p.get("habilitado") and p.get("terminos_revisados") for p in conf["portales"].values()):
            if not getattr(self, "_avisado_sin_portales", False):
                log.warning("Ningún portal autorizado (terminos_revisados: true): no se recolecta. "
                            "Se revisa la configuración cada minuto; no hace falta reiniciar.")
                self._avisado_sin_portales = True
            else:
                log.debug("Sigue sin haber portales autorizados")
            self.proximo = ahora() + dt.timedelta(minutes=1)
            return
        self._avisado_sin_portales = False
        cy.CARPETA.mkdir(parents=True, exist_ok=True)
        if REGISTRO.exists() and REGISTRO.stat().st_size > 5_000_000:
            REGISTRO.replace(REGISTRO.with_suffix(".log.1"))
        log.info("Iniciando ciclo automático de recolección (salida en %s)", REGISTRO)
        inicio_registro = REGISTRO.stat().st_size if REGISTRO.exists() else 0
        t0 = dt.datetime.now()
        entorno = {**os.environ, "PYTHONIOENCODING": "utf-8"}   # tildes sin errores en Windows
        with open(REGISTRO, "a", encoding="utf-8") as salida:
            salida.write(f"\n===== {ahora().isoformat()} =====\n")
            salida.flush()
            self.proceso = subprocess.Popen([sys.executable, "-m", "scraper.ciclo"], cwd=RAIZ,
                                            stdout=salida, stderr=subprocess.STDOUT, env=entorno)
            log.info("Ciclo lanzado como proceso %s", self.proceso.pid)
            while self.proceso.poll() is None and not self.parar.is_set():
                self.parar.wait(timeout=15)
                est = cy.leer_estado()
                log.debug("Ciclo en curso: fase=%s detalle=%s contadores=%s", est.get("fase"), est.get("detalle"),
                          est.get("contadores"))
                self.recargar()          # los avances se ven en la interfaz mientras el ciclo sigue
        if self.parar.is_set():
            log.info("Se pidió cerrar la aplicación con el ciclo en curso")
            return
        codigo = self.proceso.returncode
        duracion = (dt.datetime.now() - t0).total_seconds()
        (log.info if codigo == 0 else log.error)("Proceso del ciclo terminó con código %s en %.0fs", codigo, duracion)
        self._copiar_avisos_del_ciclo(inicio_registro)
        res = cy.leer_estado().get("ultimo_ciclo") or {}
        log.info("Resultado del ciclo: %s", {k: v for k, v in res.items() if k != "contadores"})
        if res.get("contadores"):
            log.info("Contadores del ciclo: %s", res["contadores"])
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

    def _una_vez(self, clave: str, mensaje: str):
        """La interfaz consulta el estado cada 5 s: un mismo error se registra completo una sola vez."""
        vistos = self.__dict__.setdefault("_errores_vistos", set())
        if clave in vistos:
            log.debug(mensaje, exc_info=True)
        else:
            vistos.add(clave)
            log.exception(mensaje + " (no se repetirá este aviso)")

    def _copiar_avisos_del_ciclo(self, desde: int):
        """Los avisos y errores que escribió el proceso del ciclo pasan también al registro principal
        (y a errores.log), para tener todo en un solo lugar."""
        try:
            with open(REGISTRO, "rb") as f:
                f.seek(desde)
                nuevas = f.read().decode("utf-8", "replace").splitlines()
        except OSError as e:
            log.warning("No se pudo leer %s: %s", REGISTRO, e)
            return
        importantes = [ln for ln in nuevas if any(f" {n} " in ln for n in ("WARNING", "ERROR", "CRITICAL"))
                       or ln.startswith("Traceback")]
        for ln in importantes[:200]:
            (log_ciclo.error if (" ERROR " in ln or " CRITICAL " in ln or ln.startswith("Traceback"))
             else log_ciclo.warning)("(del ciclo) %s", ln.strip())
        if not importantes:
            log.debug("El ciclo no registró avisos ni errores (%d líneas nuevas)", len(nuevas))
        elif len(importantes) > 200:
            log.warning("El ciclo registró %d avisos/errores; se copiaron los primeros 200. Vea %s",
                        len(importantes), REGISTRO)

    # ---------------------------------------------------------------- estado para la interfaz
    def estado(self) -> dict:
        conf, aj = self.ajustes()
        ciclo = cy.leer_estado()
        u = ciclo.get("ultimo_ciclo")
        latido = servicio.leer_latido()
        externo = self.hilo is not None and aj.get("segundo_plano", True) and servicio.vivo(latido)
        if externo:
            en_curso = ciclo.get("estado") == "en_curso" and latido.get("estado") == "en_curso"
            pausa = latido.get("pausa_hasta")
            pausa_hasta = dt.datetime.fromisoformat(pausa) if pausa else None
            proximo = dt.datetime.fromisoformat(latido["proximo"]) if latido.get("proximo") else None
        else:
            en_curso = bool(self.proceso and self.proceso.poll() is None)
            pausa_hasta, proximo = self.pausa_hasta, self.proximo
        autorizados = [k for k, p in conf["portales"].items() if p.get("habilitado") and p.get("terminos_revisados")]
        if self.hilo is None:
            situacion = "apagado"
        elif en_curso:
            situacion = "en_curso"
        elif pausa_hasta and ahora() < pausa_hasta:
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
            "ultimo_ciclo": {**u, "inicio": con_zona(u.get("inicio")), "fin": con_zona(u.get("fin"))} if u else None,
            "proximo_ciclo": con_zona(proximo) if proximo and not en_curso else None,
            "pausa_hasta": con_zona(pausa_hasta),
            "segundo_plano": {"activo": externo, "pid": latido.get("pid") if externo else None,
                              "desde": con_zona(latido.get("inicio")) if externo else None,
                              "ciclos": latido.get("ciclos") if externo else None,
                              "motivo": latido.get("motivo") if externo else None},
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
            self._una_vez("bd", "No se pudo abrir la base de datos para el resumen")
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
                # Ritmo real de las últimas 24 h: cuántos avisos nuevos por hora se están guardando
                "nuevos_24_h": cuenta(select(func.count()).where(t.primera_vez >= ahora() - dt.timedelta(hours=24))),
                "verificados_24_h": cuenta(select(func.count()).where(t.verificado_en >= ahora() - dt.timedelta(hours=24))),
            }

    def ejecuciones(self, n: int = 8) -> list[dict]:
        try:
            with self.motor().begin() as c:
                filas = c.execute(select(db.ejecuciones).order_by(db.ejecuciones.c.id.desc()).limit(n)).mappings()
                return [{k: (con_zona(v) if isinstance(v, dt.datetime) else v) for k, v in f.items()} for f in filas]
        except Exception:
            self._una_vez("ejecuciones", "No se pudieron leer las ejecuciones anteriores de la base de datos")
            return []
