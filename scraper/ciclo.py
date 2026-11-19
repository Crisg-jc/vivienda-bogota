"""Ciclo automático: recolecta, revisa, limpia, versiona y reentrena sin intervención.

La aplicación lo lanza sola al abrirse y cada `automatico.intervalo_horas` (config/portales.yaml).
También se puede ejecutar a mano:
  python -m scraper.ciclo                 # un ciclo completo
  python -m scraper.ciclo --sin-entrenar  # sin reentrenar el modelo al final

Fases, en orden, para cada portal autorizado (habilitado y con términos revisados):
  1. Listado: las primeras páginas en cada ciclo (ahí aparecen los avisos nuevos) y un tramo más
     del listado completo, que avanza ciclo a ciclo hasta recorrerlo todo y vuelve a empezar.
  2. Avisos nuevos: lee los enlaces que nunca se han visitado.
  3. Revisión: vuelve a visitar los avisos ya guardados cuya última visita pasó de
     `dias_entre_revisiones`. Si cambió el precio u otra característica, actualiza el registro y
     guarda el cambio en la tabla `cambios`; si el aviso ya no existe, lo marca como retirado.
  4. Dataset: si algo cambió, construye un corte nuevo solo con los avisos disponibles.
  5. Modelo: lo reentrena con ese corte.

Las reglas de cortesía no cambian: pausa de 4 a 8 s por solicitud, robots.txt, y alto total ante
403, 429 o captcha. Tras un bloqueo, la aplicación espera `espera_tras_bloqueo_horas` antes del
siguiente ciclo. El avance queda en datos/estado/ciclo.json y cada ciclo en la tabla `ejecuciones`.
"""
import argparse
import datetime as dt
import json
import logging
import os
import pathlib
import re
import time

import requests
from sqlalchemy import func, insert, or_, select, update

from limpieza import normalizar as nz

from . import db
from .extraer import CAMPOS, avisos_listado, completo, enlaces_detalle, extraer
from .http import AvisoRetirado, BloqueoDelPortal, Cliente
from .recolectar import RAIZ, cargar_conf

log = logging.getLogger("vivienda.ciclo")
CARPETA = pathlib.Path(os.environ.get("VIVIENDA_ESTADO", RAIZ / "datos" / "estado"))
ESTADO = CARPETA / "ciclo.json"
CANDADO = CARPETA / "ciclo.lock"

# Campos cuyo cambio se registra en el historial, con la función que los compara
SEGUIDOS = {"precio": nz.precio, "area": nz.area, "administracion": nz.precio,
            "habitaciones": nz.entero, "banos": nz.entero, "estrato": nz.entero,
            "parqueaderos": nz.entero, "antiguedad": nz.antiguedad, "estado": nz.estado}

POR_DEFECTO = {"activo": True, "intervalo_horas": 2, "primer_ciclo_tras_segundos": 30,
               "paginas_iniciales_siempre": 5, "paginas_por_ciclo": 100, "nuevos_por_ciclo": 600,
               # Con más de esto por leer, el ciclo no recorre el resto del listado: lee lo pendiente
               "pendientes_para_pausar_listado": 300,
               # true: un recolector en segundo plano sigue alimentando la base con la app cerrada
               "segundo_plano": True,
               "revisiones_por_ciclo": 400, "dias_entre_revisiones": 3, "dias_vigencia": 7,
               "max_minutos_por_ciclo": 240,
               "espera_tras_bloqueo_horas": 6, "max_errores_seguidos": 25, "versiones_csv_a_conservar": 10}


def ajustes(conf: dict) -> dict:
    return {**POR_DEFECTO, **(conf.get("automatico") or {})}


DEMANDA = CARPETA / "demanda.json"
PARAR = CARPETA / "parar"


def leer_demanda() -> dict[str, int]:
    """Localidades donde faltaron comparables en las últimas 72 h, con cuántas veces pasó."""
    try:
        d = json.loads(DEMANDA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    limite = (dt.datetime.now() - dt.timedelta(hours=72)).isoformat()
    return {k: v["n"] for k, v in d.items() if v.get("ultima", "") >= limite}


def anotar_demanda(localidad: str) -> None:
    """Lo llama la app cuando una estimación salió sin comparables o con confiabilidad baja."""
    try:
        d = json.loads(DEMANDA.read_text(encoding="utf-8")) if DEMANDA.exists() else {}
    except ValueError:
        d = {}
    e = d.get(localidad, {"n": 0})
    d[localidad] = {"n": e["n"] + 1, "ultima": dt.datetime.now().isoformat(timespec="seconds")}
    CARPETA.mkdir(parents=True, exist_ok=True)
    DEMANDA.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def ahora() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


# ---------------------------------------------------------------- estado en disco

def leer_estado() -> dict:
    try:
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log.warning("No se pudo leer %s (%s): se toma como vacío", ESTADO, e)
        return {}


def escribir_estado(**cambios) -> dict:
    """Mezcla y guarda de forma atómica (la app lo lee mientras el ciclo escribe)."""
    CARPETA.mkdir(parents=True, exist_ok=True)
    datos = {**leer_estado(), **cambios, "actualizado": ahora().isoformat()}
    tmp = ESTADO.with_suffix(".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, ESTADO)
    return datos


def tomar_candado(max_minutos: int) -> bool:
    """El ciclo en curso renueva el candado en cada paso (menos de un minuto entre pasos). Uno que
    lleva más de 10 minutos sin renovarse es de un ciclo que se cortó (app cerrada, PC apagado)."""
    CARPETA.mkdir(parents=True, exist_ok=True)
    if CANDADO.exists():
        edad = time.time() - CANDADO.stat().st_mtime
        if edad < 10 * 60:
            log.info("Otro ciclo tiene el candado (renovado hace %.0fs): no se inicia otro", edad)
            return False          # otro ciclo sigue en curso
        log.warning("Candado abandonado (%.0f min sin renovarse, ciclo cortado): se libera", edad / 60)
        CANDADO.unlink(missing_ok=True)
    CANDADO.write_text(f"{os.getpid()} {ahora().isoformat()}", encoding="utf-8")
    log.debug("Candado tomado por el proceso %s", os.getpid())
    return True


def soltar_candado():
    CANDADO.unlink(missing_ok=True)
    log.debug("Candado liberado")


# ---------------------------------------------------------------- el ciclo

class Presupuesto:
    def __init__(self, minutos):
        self.fin = time.monotonic() + minutos * 60

    def agotado(self) -> bool:
        if PARAR.exists():          # se pidió detener el recolector: el ciclo termina ordenadamente
            return True
        return time.monotonic() > self.fin


class Ciclo:
    def __init__(self, conf: dict, cliente_cls=Cliente, eng=None):
        self.conf, self.aj = conf, ajustes(conf)
        self.cli = cliente_cls(conf["general"])
        self.eng = eng or db.motor()
        self.presupuesto = Presupuesto(self.aj["max_minutos_por_ciclo"])
        self.n = {"enlaces_nuevos": 0, "avisos_nuevos": 0, "revisados": 0, "con_cambios": 0,
                  "retirados": 0, "verificados_listado": 0, "desde_listado": 0, "errores": 0}

    def progreso(self, fase: str, detalle: str = ""):
        log.debug("Progreso: %s | %s | %s", fase, detalle, self.n)
        escribir_estado(estado="en_curso", fase=fase, detalle=detalle, contadores=self.n)
        if CANDADO.exists():
            os.utime(CANDADO)     # "sigo vivo"

    # ---- fase 1
    def listado(self, nombre: str, p: dict, solo_iniciales: bool = False):
        est = leer_estado()
        cursor = max(int((est.get("cursor") or {}).get(nombre, 0)), self.aj["paginas_iniciales_siempre"] + 1)
        iniciales = list(range(1, self.aj["paginas_iniciales_siempre"] + 1))
        tramo = [] if solo_iniciales else list(range(cursor, cursor + self.aj["paginas_por_ciclo"]))
        if solo_iniciales:
            log.info("Hay muchos avisos por leer: del listado solo se miran las primeras %d páginas (lo más nuevo)",
                     len(iniciales))
        vacias, max_vacias = 0, self.conf["general"].get("max_paginas_vacias_seguidas", 5)
        siguiente = cursor
        if tramo:
            log.info("Listado de %s: páginas 1-%d y tramo %d-%d", nombre, len(iniciales), tramo[0], tramo[-1])
        for n in iniciales + tramo:
            if self.presupuesto.agotado():
                log.warning("Se acabó el tiempo del ciclo (%s min) en la página %d del listado",
                            self.aj["max_minutos_por_ciclo"], n)
                break
            url = p["listado_pagina_1"] if n == 1 else p["listado_pagina_n"].format(pagina=n)
            self.progreso("Buscando avisos nuevos en el listado", f"{nombre}, página {n}")
            html = self.cli.get(url, p["base"])
            links = enlaces_detalle(html, p["base"], p["patron_detalle"])
            if n in tramo:
                siguiente = n + 1
            if not links:
                vacias += 1
                log.info("Página %d de %s: 0 avisos individuales (%d vacías seguidas; tamaño %d caracteres)",
                         n, nombre, vacias, len(html))
                if n in tramo and vacias >= max_vacias:
                    log.info("Fin del listado de %s en la página %d: el próximo ciclo vuelve a empezar", nombre, n)
                    siguiente = 0          # fin del listado: el próximo ciclo vuelve a empezar
                    break
                continue
            vacias = 0
            with self.eng.begin() as c:
                ya = set(c.scalars(select(db.enlaces.c.url).where(db.enlaces.c.url.in_(links))))
                filas = [{"url": u, "portal": nombre, "procesado": False, "disponible": True}
                         for u in dict.fromkeys(links) if u not in ya]
                if filas:
                    c.execute(insert(db.enlaces), filas)
            self.n["enlaces_nuevos"] += len(filas)
            tarjetas = avisos_listado(html, p["base"], p["patron_detalle"], p.get("patron_barrio_url"))
            self.verificar_desde_listado({u: c["precio"] for u, c in tarjetas.items()})
            self.guardar_desde_listado(nombre, tarjetas)
            log.info("Página %d de %s: %d avisos, %d nuevos", n, nombre, len(links), len(filas))
            log.debug("Enlaces nuevos de la página %d: %s", n, [f["url"] for f in filas])
        log.info("Listado de %s terminado: %d enlaces nuevos; el próximo ciclo sigue en la página %d",
                 nombre, self.n["enlaces_nuevos"], siguiente)
        escribir_estado(cursor={**(leer_estado().get("cursor") or {}), nombre: siguiente})

    def guardar_desde_listado(self, nombre: str, tarjetas: dict[str, dict]):
        """Avisos nuevos cuyos datos vienen completos en la tarjeta del listado: se guardan sin abrirlos.
        Los incompletos se guardan igual (sirven para contar y verificar) y quedan en la cola para
        completarse abriendo el aviso."""
        if not tarjetas:
            return
        t = db.inmuebles_crudos.c
        with self.eng.begin() as c:
            ya = set(c.scalars(select(t.url_origen).where(t.url_origen.in_(list(tarjetas)))))
        completos = []
        for url, campos in tarjetas.items():
            if url in ya or not campos.get("area"):
                continue
            self.guardar(nombre, url, campos, "listado")
            self.n["desde_listado"] += 1
            if completo(campos):
                completos.append(url)
        if completos:
            with self.eng.begin() as c:
                e = db.enlaces.c
                c.execute(update(db.enlaces).where(e.url.in_(completos)).values(
                    procesado=True, ultimo_estado="ok-listado", ultima_visita=ahora(), disponible=True))
        log.debug("Desde el listado: %d guardados, %d completos sin abrir el aviso",
                  self.n["desde_listado"], len(completos))

    def verificar_desde_listado(self, tarjetas: dict[str, str]):
        """Avisos ya guardados que aparecen en el listado: si el precio es el mismo, quedan
        verificados sin abrirlos (una visita menos al portal por aviso). Si el precio cambió, se
        ponen primeros en la cola de revisión, que abre el aviso y actualiza todos sus datos."""
        if not tarjetas:
            return
        t, e = db.inmuebles_crudos.c, db.enlaces.c
        iguales, distintos = [], []
        with self.eng.begin() as c:
            guardados = c.execute(select(t.url_origen, t.precio_txt, t.disponible)
                                  .where(t.url_origen.in_(list(tarjetas)))).all()
            for url, precio_txt, disponible in guardados:
                antes, ahora_ = nz.precio(precio_txt), nz.precio(tarjetas[url])
                if ahora_ is not None and antes == ahora_ and disponible is not False:
                    iguales.append(url)
                else:
                    distintos.append(url)
                    log.info("Precio distinto en el listado (%s -> %s): se revisa el aviso %s", precio_txt,
                             tarjetas[url], url)
            momento = ahora()
            if iguales:
                c.execute(update(db.inmuebles_crudos).where(t.url_origen.in_(iguales)).values(verificado_en=momento))
                c.execute(update(db.enlaces).where(e.url.in_(iguales)).values(ultima_visita=momento,
                                                                               ultimo_estado="ok-listado"))
            if distintos:     # sin última visita = primeros en por_revisar
                c.execute(update(db.enlaces).where(e.url.in_(distintos)).values(ultima_visita=None))
        self.n["verificados_listado"] += len(iguales)
        log.debug("Desde el listado: %d verificados sin cambios, %d con precio distinto, %d nuevos o sin guardar",
                  len(iguales), len(distintos), len(tarjetas) - len(guardados))

    # ---- fases 2 y 3
    def pendientes(self, nombre: str, limite: int) -> list[str]:
        """Avisos por leer. Primero los de las localidades donde los usuarios pidieron estimaciones
        y faltaron comparables (datos/estado/demanda.json); luego los más antiguos de la cola."""
        with self.eng.begin() as c:
            urls = list(c.scalars(select(db.enlaces.c.url)
                                  .where(db.enlaces.c.portal == nombre, db.enlaces.c.procesado.is_(False))
                                  .order_by(db.enlaces.c.descubierto_en)))
        demanda = leer_demanda()
        if demanda and urls:
            patron = self.conf["portales"][nombre].get("patron_barrio_url")

            def prioridad(u):
                m = re.search(patron, u) if patron else None
                loc = nz.localidad(None, m.group(1).replace("-", " ")) if m else None
                return -demanda.get(loc, 0)
            urls.sort(key=prioridad)      # sort estable: dentro de cada grupo se respeta la antigüedad
            log.info("Prioridad por demanda de los usuarios: %s", demanda)
        return urls[:limite]

    def contar_pendientes(self, nombre: str) -> int:
        with self.eng.begin() as c:
            return c.scalar(select(func.count()).where(db.enlaces.c.portal == nombre,
                                                       db.enlaces.c.procesado.is_(False))) or 0

    def por_revisar(self, nombre: str, limite: int) -> list[str]:
        corte = ahora() - dt.timedelta(days=self.aj["dias_entre_revisiones"])
        e = db.enlaces.c
        with self.eng.begin() as c:
            return list(c.scalars(
                select(e.url).where(e.portal == nombre, e.procesado.is_(True),
                                    or_(e.disponible.is_(None), e.disponible.is_(True)),
                                    or_(e.ultimo_estado.is_(None), e.ultimo_estado != "robots"),
                                    or_(e.ultima_visita.is_(None), e.ultima_visita < corte))
                .order_by(e.ultima_visita.is_(None).desc(), e.ultima_visita).limit(limite)))

    def recorrer(self, nombre: str, p: dict, urls: list[str], fase: str, revision: bool):
        seguidos = 0
        log.info("%s en %s: %d por visitar", fase, nombre, len(urls))
        for i, url in enumerate(urls, 1):
            if self.presupuesto.agotado():
                log.warning("Se acabó el tiempo del ciclo: quedaron %d sin visitar en '%s'", len(urls) - i + 1, fase)
                break
            self.progreso(fase, f"{nombre}: {i} de {len(urls)}")
            ok = self.visitar(nombre, p, url)
            if revision:
                self.n["revisados"] += 1
            seguidos = 0 if ok else seguidos + 1
            if i % 25 == 0:
                log.info("%s en %s: %d de %d (%s)", fase, nombre, i, len(urls), self.n)
            if seguidos >= self.aj["max_errores_seguidos"]:
                log.error("%d avisos seguidos sin datos en %s: se detiene el portal", seguidos, nombre)
                raise RuntimeError(f"{seguidos} avisos seguidos sin datos en {nombre}: "
                                   "probablemente el portal cambió su página. Revise con 'diagnostico'.")

    def visitar(self, nombre: str, p: dict, url: str) -> bool:
        """Visita un aviso, lo guarda o lo actualiza. Devuelve False si no se pudo leer."""
        estado, ok = "ok", True
        try:
            html, final = self.cli.obtener(url, p["base"])
            if final and final.rstrip("/") != url.rstrip("/") and not re.match(p["patron_detalle"], final):
                raise AvisoRetirado(f"redirigido a {final}")
            campos, metodo = extraer(html, url, p.get("patron_barrio_url"))
            faltan = [k for k in CAMPOS if campos.get(k) in (None, "")]
            log.debug("Aviso %s leído por %s; faltan: %s", url, metodo, faltan or "nada")
            if not campos.get("precio") and not campos.get("area"):
                log.warning("Aviso sin precio ni área (¿cambió la página del portal?): %s", url)
                estado, ok = "sin_datos", False
            else:
                self.guardar(nombre, url, campos, metodo)
        except AvisoRetirado as e:
            log.info("Aviso retirado (%s): %s", e, url)
            self.retirar(url)
            estado = "retirado"
        except PermissionError as e:
            log.info("No se visita por robots.txt: %s (%s)", url, e)
            estado = "robots"
        except BloqueoDelPortal as e:
            log.error("BLOQUEO del portal %s en %s: %s. Se detiene todo el ciclo.", nombre, url, e)
            raise
        except Exception as e:  # un aviso dañado no debe tumbar el ciclo
            log.warning("Error leyendo %s: %s: %s", url, type(e).__name__, e, exc_info=log.isEnabledFor(logging.DEBUG))
            estado, ok = f"error:{type(e).__name__}"[:40], False
        if not ok:
            self.n["errores"] += 1
        with self.eng.begin() as c:
            e = db.enlaces.c
            c.execute(update(db.enlaces).where(e.url == url).values(
                procesado=True, ultimo_estado=estado, ultima_visita=ahora(),
                visitas=func.coalesce(e.visitas, 0) + 1,
                disponible=estado != "retirado"))
        return ok

    def guardar(self, nombre: str, url: str, campos: dict, metodo: str):
        nuevo = {f"{k}_txt": campos.get(k) for k in CAMPOS}
        t = db.inmuebles_crudos
        with self.eng.begin() as c:
            previo = c.execute(select(t).where(t.c.url_origen == url)).mappings().first()
            momento = ahora()
            base = {"portal": nombre, "url_origen": url, "fecha_consulta": dt.date.today(),
                    "metodo_extraccion": metodo, "verificado_en": momento,
                    "disponible": True, "retirado_en": None}
            if previo is None:
                c.execute(insert(t), {**nuevo, **base, "primera_vez": momento, "actualizado_en": momento})
                if metodo == "listado":
                    self.n["avisos_nuevos"] += 1
                    return
                self.n["avisos_nuevos"] += 1
                log.debug("Aviso nuevo guardado: %s precio=%s área=%s", url, nuevo.get("precio_txt"), nuevo.get("area_txt"))
                return
            cambios = []
            # Completar un aviso guardado desde la tarjeta del listado no es un "cambio": la tarjeta y la
            # página del aviso pueden leer distinto el mismo dato (área construida vs. privada, etc.).
            completando = str(previo.get("metodo_extraccion") or "") == "listado" and metodo != "listado"
            for campo, f in ({} if completando else SEGUIDOS).items():
                antes, despues = previo[f"{campo}_txt"], nuevo[f"{campo}_txt"]
                if despues is not None and f(despues) is not None and f(despues) != f(antes):
                    cambios.append({"url_origen": url, "fecha": ahora(), "campo": campo,
                                    "antes": antes, "despues": despues})
            if previo["disponible"] is False:
                cambios.append({"url_origen": url, "fecha": ahora(), "campo": "disponible",
                                "antes": "no", "despues": "sí"})
            # Un campo que esta vez no se pudo leer conserva el valor anterior
            fusion = {k: (v if v is not None else previo[k]) for k, v in nuevo.items()}
            if cambios:
                base["actualizado_en"] = momento        # solo cambia cuando algo cambió de verdad
            if previo.get("primera_vez") is None:
                base["primera_vez"] = previo.get("actualizado_en") or momento
            c.execute(update(t).where(t.c.id == previo["id"]).values(**fusion, **base))
            if cambios:
                c.execute(insert(db.cambios), cambios)
                self.n["con_cambios"] += 1
                log.info("Cambios en %s: %s", url, "; ".join(f"{x['campo']}: {x['antes']} -> {x['despues']}"
                                                            for x in cambios))
            else:
                log.debug("Revisado sin cambios: %s", url)

    def retirar(self, url: str):
        t = db.inmuebles_crudos
        with self.eng.begin() as c:
            previo = c.execute(select(t.c.id, t.c.disponible).where(t.c.url_origen == url)).first()
            if previo and previo.disponible is not False:
                c.execute(update(t).where(t.c.id == previo.id).values(disponible=False, retirado_en=ahora(),
                                                                      verificado_en=ahora(), actualizado_en=ahora()))
                c.execute(insert(db.cambios), [{"url_origen": url, "fecha": ahora(), "campo": "disponible",
                                                "antes": "sí", "despues": "no"}])
                self.n["retirados"] += 1
                log.debug("Marcado como retirado en la base: %s", url)

    # ---- todo junto
    def portales(self) -> list[tuple[str, dict]]:
        return [(k, p) for k, p in self.conf["portales"].items()
                if p.get("habilitado") and p.get("terminos_revisados")]

    def recolectar(self):
        for nombre, p in self.portales():
            pendientes = self.contar_pendientes(nombre)
            self.listado(nombre, p, solo_iniciales=pendientes > self.aj["pendientes_para_pausar_listado"])
            self.recorrer(nombre, p, self.pendientes(nombre, self.aj["nuevos_por_ciclo"]),
                          "Leyendo avisos nuevos", revision=False)
            self.recorrer(nombre, p, self.por_revisar(nombre, self.aj["revisiones_por_ciclo"]),
                          "Revisando cambios en avisos guardados", revision=True)


def ejecutar(conf: dict | None = None, cliente_cls=Cliente, entrenar_modelo: bool = True) -> dict:
    conf = conf or cargar_conf()
    aj = ajustes(conf)
    log.info("==== Ciclo automático: inicio (pid %s) ====", os.getpid())
    log.debug("Ajustes: %s", aj)
    if not tomar_candado(aj["max_minutos_por_ciclo"]):
        return {"resultado": "en_curso", "mensaje": "Ya hay un ciclo en curso."}
    inicio = ahora()
    ciclo = Ciclo(conf, cliente_cls)
    res = {"inicio": inicio.isoformat(), "resultado": "completo", "mensaje": "", "version_dataset": None}
    try:
        if not ciclo.portales():
            res.update(resultado="sin_portales",
                       mensaje="Ningún portal tiene los términos de uso revisados en config/portales.yaml.")
        else:
            try:
                ciclo.recolectar()
            except BloqueoDelPortal as e:
                log.error("Ciclo detenido por bloqueo del portal: %s", e)
                res.update(resultado="bloqueado", mensaje=f"El portal limitó el acceso ({e}). Se espera antes de seguir.")
            except RuntimeError as e:
                log.error("Ciclo detenido: %s", e)
                res.update(resultado="error", mensaje=str(e))
            except requests.exceptions.ConnectionError as e:
                log.error("Sin conexión con el portal: %s", e)
                res.update(resultado="sin_conexion",
                           mensaje="No hubo conexión con el portal (revise internet). Se reintenta en una hora.")
        cambio = ciclo.n["avisos_nuevos"] + ciclo.n["con_cambios"] + ciclo.n["retirados"]
        res["version_dataset"] = actualizar_dataset_y_modelo(ciclo, cambio > 0, entrenar_modelo, aj)
    except Exception as e:  # el estado debe quedar escrito aunque algo falle
        log.exception("Error inesperado en el ciclo")
        res.update(resultado="error", mensaje=f"{type(e).__name__}: {e}")
    finally:
        res.update(fin=ahora().isoformat(), **ciclo.n)
        with ciclo.eng.begin() as c:
            c.execute(insert(db.ejecuciones), [{
                "inicio": inicio, "fin": ahora(), "resultado": res["resultado"], "mensaje": res["mensaje"],
                "version_dataset": res["version_dataset"],
                **{k: v for k, v in ciclo.n.items() if k != "errores"}}])
        escribir_estado(estado="inactivo", fase="", detalle="", contadores=ciclo.n, ultimo_ciclo=res)
        soltar_candado()
        log.info("==== Ciclo automático: fin (%s) %s ====", res["resultado"], ciclo.n)
    return res


def actualizar_dataset_y_modelo(ciclo: Ciclo, hubo_cambios: bool, entrenar_modelo: bool, aj: dict):
    from limpieza import construir_dataset as cd
    hay_csv = any(RAIZ.joinpath("datos").glob("dataset_v*.csv"))
    if not hubo_cambios and hay_csv:
        log.info("Sin cambios en los avisos: no se construye un dataset nuevo")
        return None
    if not hay_csv:
        log.info("No hay ningún dataset_vN.csv en datos/: se construye uno aunque el contenido no haya cambiado")
    ciclo.progreso("Limpiando y versionando el dataset")
    try:
        corte = cd.construir(notas="Ciclo automático", solo_si_cambia=hay_csv, eng=ciclo.eng,
                             conservar=aj["versiones_csv_a_conservar"])
    except SystemExit as e:           # todavía no hay avisos guardados
        log.info("No se construyó el dataset: %s", e)
        return None
    if corte is None:
        log.info("El dataset quedó igual al anterior: no se publica versión nueva")
        return None
    version, ruta, n = corte
    log.info("Dataset v%s publicado: %s con %d avisos", version, ruta.name, n)
    if entrenar_modelo:
        from modelo import entrenar
        ciclo.progreso("Reentrenando el modelo", f"{n} avisos")
        try:
            entrenar.entrenar(ruta, "real")
        except SystemExit as e:      # muy pocos avisos todavía: el dataset queda, el modelo espera
            log.warning("No se reentrenó el modelo: %s", e)
            escribir_estado(mensaje_modelo=str(e))
    return version


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sin-entrenar", action="store_true")
    a = ap.parse_args(argv)
    from comun import registro
    registro.configurar("ciclo", archivo=False)   # su salida la guarda la app en datos/estado/ciclo.log
    r = ejecutar(entrenar_modelo=not a.sin_entrenar)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
