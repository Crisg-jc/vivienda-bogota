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
import os
import re
import time

import requests
from sqlalchemy import func, insert, or_, select, update

from limpieza import normalizar as nz

from . import db
from .extraer import CAMPOS, enlaces_detalle, extraer
from .http import AvisoRetirado, BloqueoDelPortal, Cliente
from .recolectar import RAIZ, cargar_conf

CARPETA = RAIZ / "datos" / "estado"
ESTADO = CARPETA / "ciclo.json"
CANDADO = CARPETA / "ciclo.lock"

# Campos cuyo cambio se registra en el historial, con la función que los compara
SEGUIDOS = {"precio": nz.precio, "area": nz.area, "administracion": nz.precio,
            "habitaciones": nz.entero, "banos": nz.entero, "estrato": nz.entero,
            "parqueaderos": nz.entero, "antiguedad": nz.antiguedad, "estado": nz.estado}

POR_DEFECTO = {"activo": True, "intervalo_horas": 12, "primer_ciclo_tras_segundos": 30,
               "paginas_iniciales_siempre": 5, "paginas_por_ciclo": 100, "nuevos_por_ciclo": 600,
               "revisiones_por_ciclo": 300, "dias_entre_revisiones": 7, "max_minutos_por_ciclo": 240,
               "espera_tras_bloqueo_horas": 6, "max_errores_seguidos": 25, "versiones_csv_a_conservar": 10}


def ajustes(conf: dict) -> dict:
    return {**POR_DEFECTO, **(conf.get("automatico") or {})}


def ahora() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


# ---------------------------------------------------------------- estado en disco

def leer_estado() -> dict:
    try:
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
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
            return False          # otro ciclo sigue en curso
        CANDADO.unlink(missing_ok=True)
    CANDADO.write_text(f"{os.getpid()} {ahora().isoformat()}", encoding="utf-8")
    return True


def soltar_candado():
    CANDADO.unlink(missing_ok=True)


# ---------------------------------------------------------------- el ciclo

class Presupuesto:
    def __init__(self, minutos):
        self.fin = time.monotonic() + minutos * 60

    def agotado(self) -> bool:
        return time.monotonic() > self.fin


class Ciclo:
    def __init__(self, conf: dict, cliente_cls=Cliente, eng=None):
        self.conf, self.aj = conf, ajustes(conf)
        self.cli = cliente_cls(conf["general"])
        self.eng = eng or db.motor()
        self.presupuesto = Presupuesto(self.aj["max_minutos_por_ciclo"])
        self.n = {"enlaces_nuevos": 0, "avisos_nuevos": 0, "revisados": 0, "con_cambios": 0,
                  "retirados": 0, "errores": 0}

    def progreso(self, fase: str, detalle: str = ""):
        escribir_estado(estado="en_curso", fase=fase, detalle=detalle, contadores=self.n)
        if CANDADO.exists():
            os.utime(CANDADO)     # "sigo vivo"

    # ---- fase 1
    def listado(self, nombre: str, p: dict):
        est = leer_estado()
        cursor = max(int((est.get("cursor") or {}).get(nombre, 0)), self.aj["paginas_iniciales_siempre"] + 1)
        iniciales = list(range(1, self.aj["paginas_iniciales_siempre"] + 1))
        tramo = list(range(cursor, cursor + self.aj["paginas_por_ciclo"]))
        vacias, max_vacias = 0, self.conf["general"].get("max_paginas_vacias_seguidas", 5)
        siguiente = cursor
        for n in iniciales + tramo:
            if self.presupuesto.agotado():
                break
            url = p["listado_pagina_1"] if n == 1 else p["listado_pagina_n"].format(pagina=n)
            self.progreso("Buscando avisos nuevos en el listado", f"{nombre}, página {n}")
            html = self.cli.get(url, p["base"])
            links = enlaces_detalle(html, p["base"], p["patron_detalle"])
            if n in tramo:
                siguiente = n + 1
            if not links:
                vacias += 1
                if n in tramo and vacias >= max_vacias:
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
        escribir_estado(cursor={**(leer_estado().get("cursor") or {}), nombre: siguiente})

    # ---- fases 2 y 3
    def pendientes(self, nombre: str, limite: int) -> list[str]:
        with self.eng.begin() as c:
            return list(c.scalars(select(db.enlaces.c.url)
                                  .where(db.enlaces.c.portal == nombre, db.enlaces.c.procesado.is_(False))
                                  .order_by(db.enlaces.c.descubierto_en).limit(limite)))

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
        for i, url in enumerate(urls, 1):
            if self.presupuesto.agotado():
                break
            self.progreso(fase, f"{nombre}: {i} de {len(urls)}")
            ok = self.visitar(nombre, p, url)
            if revision:
                self.n["revisados"] += 1
            seguidos = 0 if ok else seguidos + 1
            if seguidos >= self.aj["max_errores_seguidos"]:
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
            if not campos.get("precio") and not campos.get("area"):
                estado, ok = "sin_datos", False
            else:
                self.guardar(nombre, url, campos, metodo)
        except AvisoRetirado:
            self.retirar(url)
            estado = "retirado"
        except PermissionError:
            estado = "robots"
        except BloqueoDelPortal:
            raise
        except Exception as e:  # un aviso dañado no debe tumbar el ciclo
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
            base = {"portal": nombre, "url_origen": url, "fecha_consulta": dt.date.today(),
                    "metodo_extraccion": metodo, "actualizado_en": ahora(),
                    "disponible": True, "retirado_en": None}
            if previo is None:
                c.execute(insert(t), {**nuevo, **base})
                self.n["avisos_nuevos"] += 1
                return
            cambios = []
            for campo, f in SEGUIDOS.items():
                antes, despues = previo[f"{campo}_txt"], nuevo[f"{campo}_txt"]
                if despues is not None and f(despues) is not None and f(despues) != f(antes):
                    cambios.append({"url_origen": url, "fecha": ahora(), "campo": campo,
                                    "antes": antes, "despues": despues})
            if previo["disponible"] is False:
                cambios.append({"url_origen": url, "fecha": ahora(), "campo": "disponible",
                                "antes": "no", "despues": "sí"})
            # Un campo que esta vez no se pudo leer conserva el valor anterior
            fusion = {k: (v if v is not None else previo[k]) for k, v in nuevo.items()}
            c.execute(update(t).where(t.c.id == previo["id"]).values(**fusion, **base))
            if cambios:
                c.execute(insert(db.cambios), cambios)
                self.n["con_cambios"] += 1

    def retirar(self, url: str):
        t = db.inmuebles_crudos
        with self.eng.begin() as c:
            previo = c.execute(select(t.c.id, t.c.disponible).where(t.c.url_origen == url)).first()
            if previo and previo.disponible is not False:
                c.execute(update(t).where(t.c.id == previo.id).values(disponible=False, retirado_en=ahora()))
                c.execute(insert(db.cambios), [{"url_origen": url, "fecha": ahora(), "campo": "disponible",
                                                "antes": "sí", "despues": "no"}])
                self.n["retirados"] += 1

    # ---- todo junto
    def portales(self) -> list[tuple[str, dict]]:
        return [(k, p) for k, p in self.conf["portales"].items()
                if p.get("habilitado") and p.get("terminos_revisados")]

    def recolectar(self):
        for nombre, p in self.portales():
            self.listado(nombre, p)
            self.recorrer(nombre, p, self.pendientes(nombre, self.aj["nuevos_por_ciclo"]),
                          "Leyendo avisos nuevos", revision=False)
            self.recorrer(nombre, p, self.por_revisar(nombre, self.aj["revisiones_por_ciclo"]),
                          "Revisando cambios en avisos guardados", revision=True)


def ejecutar(conf: dict | None = None, cliente_cls=Cliente, entrenar_modelo: bool = True) -> dict:
    conf = conf or cargar_conf()
    aj = ajustes(conf)
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
                res.update(resultado="bloqueado", mensaje=f"El portal limitó el acceso ({e}). Se espera antes de seguir.")
            except RuntimeError as e:
                res.update(resultado="error", mensaje=str(e))
            except requests.exceptions.ConnectionError:
                res.update(resultado="sin_conexion",
                           mensaje="No hubo conexión con el portal (revise internet). Se reintenta en una hora.")
        cambio = ciclo.n["avisos_nuevos"] + ciclo.n["con_cambios"] + ciclo.n["retirados"]
        res["version_dataset"] = actualizar_dataset_y_modelo(ciclo, cambio > 0, entrenar_modelo, aj)
    except Exception as e:  # el estado debe quedar escrito aunque algo falle
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
    return res


def actualizar_dataset_y_modelo(ciclo: Ciclo, hubo_cambios: bool, entrenar_modelo: bool, aj: dict):
    from limpieza import construir_dataset as cd
    hay_csv = any(RAIZ.joinpath("datos").glob("dataset_v*.csv"))
    if not hubo_cambios and hay_csv:
        return None
    ciclo.progreso("Limpiando y versionando el dataset")
    try:
        corte = cd.construir(notas="Ciclo automático", solo_si_cambia=True, eng=ciclo.eng,
                             conservar=aj["versiones_csv_a_conservar"])
    except SystemExit:           # todavía no hay avisos guardados
        return None
    if corte is None:
        return None
    version, ruta, n = corte
    if entrenar_modelo:
        from modelo import entrenar
        ciclo.progreso("Reentrenando el modelo", f"{n} avisos")
        try:
            entrenar.entrenar(ruta, "real")
        except SystemExit as e:      # muy pocos avisos todavía: el dataset queda, el modelo espera
            escribir_estado(mensaje_modelo=str(e))
    return version


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sin-entrenar", action="store_true")
    a = ap.parse_args(argv)
    r = ejecutar(entrenar_modelo=not a.sin_entrenar)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
