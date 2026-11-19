"""Diagnóstico: revisa todo lo que la aplicación necesita y dice qué falla y cómo arreglarlo.

    python -m app.diagnostico            # revisión completa, incluida la conexión con los portales
    python -m app.diagnostico --sin-red  # sin salir a internet

Guarda el informe en datos/registros/diagnostico-AAAAMMDD-HHMMSS.txt para poder enviarlo.
También está en la aplicación: http://127.0.0.1:8000/api/diagnostico (solo en este computador).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import shutil
import socket
import sys
import time

from comun import registro

log = logging.getLogger("vivienda.diagnostico")
RAIZ = registro.RAIZ


class Informe:
    def __init__(self):
        self.items: list[dict] = []

    def agregar(self, grupo: str, nombre: str, estado: str, detalle: str = "", arreglo: str = ""):
        self.items.append({"grupo": grupo, "revision": nombre, "estado": estado, "detalle": detalle,
                           "arreglo": arreglo})
        nivel = {"ok": logging.INFO, "aviso": logging.WARNING, "error": logging.ERROR}[estado]
        log.log(nivel, "[%s] %s: %s%s", grupo, nombre, detalle or estado, f" -> {arreglo}" if arreglo else "")

    def ok(self, g, n, d=""):
        self.agregar(g, n, "ok", d)

    def aviso(self, g, n, d, a=""):
        self.agregar(g, n, "aviso", d, a)

    def error(self, g, n, d, a=""):
        self.agregar(g, n, "error", d, a)


def _seguro(inf: Informe, grupo: str, nombre: str, fn):
    """Una revisión que falla no detiene las demás: se anota el error con su causa."""
    try:
        fn()
    except Exception as e:
        log.debug("Detalle del fallo en %s/%s", grupo, nombre, exc_info=True)
        inf.error(grupo, nombre, f"{type(e).__name__}: {e}", "Vea el detalle en datos/registros/vivienda.log")


def revisar(con_red: bool = True) -> dict:
    inf = Informe()
    t0 = time.perf_counter()
    # Los errores previos se leen antes de revisar, para no contar los que escribe este mismo diagnóstico.
    errores_previos = [ln for ln in registro.ultimas_lineas(200, solo_errores=True)
                       if " ERROR " in ln or " CRITICAL " in ln]
    ent = registro.entorno()

    # ---------------------------------------------------------------- Python y librerías
    v = sys.version_info
    if v >= (3, 11):
        inf.ok("python", "Versión", f"Python {ent['python']} ({ent['ejecutable']})")
    else:
        inf.error("python", "Versión", f"Python {ent['python']}", "Instale Python 3.11 o más reciente (3.12 o 3.13).")
    if ".venv" in ent["ejecutable"] or "venv" in ent["ejecutable"] or os.environ.get("VIRTUAL_ENV"):
        inf.ok("python", "Entorno virtual", "en uso")
    else:
        inf.aviso("python", "Entorno virtual", "se está usando el Python general del sistema",
                  "Use .venv\\Scripts\\python.exe (Windows) o .venv/bin/python, o ejecute iniciar.bat")
    faltan = [k for k, val in ent["paquetes"].items() if val == "NO INSTALADO"]
    if faltan:
        inf.error("python", "Paquetes", "faltan: " + ", ".join(faltan),
                  "python -m pip install -r requirements.txt")
    else:
        inf.ok("python", "Paquetes", ", ".join(f"{k} {val}" for k, val in ent["paquetes"].items()))

    # ---------------------------------------------------------------- carpetas y archivos
    for rel in ("app/static/index.html", "app/static/app.js", "app/static/estilos.css",
                "app/static/vendor/leaflet/leaflet.js", "app/static/geo/localidades.geojson",
                "app/static/geo/region.geojson", "config/portales.yaml", "requirements.txt"):
        ruta = RAIZ / rel
        if ruta.exists() and ruta.stat().st_size > 0:
            inf.ok("archivos", rel, f"{ruta.stat().st_size:,} bytes")
        else:
            inf.error("archivos", rel, "no existe o está vacío",
                      "Vuelva a descomprimir el zip completo (Expand-Archive ... -Force)")

    def escribir():
        prueba = RAIZ / "datos" / ".prueba_escritura"
        prueba.parent.mkdir(exist_ok=True)
        prueba.write_text("ok", encoding="utf-8")
        prueba.unlink()
        libre = shutil.disk_usage(RAIZ).free / 1e9
        (inf.ok if libre > 1 else inf.aviso)("archivos", "Escritura en datos/",
                                            f"se puede escribir; {libre:.1f} GB libres",
                                            *([] if libre > 1 else ["Libere espacio en el disco"]))
    _seguro(inf, "archivos", "Escritura en datos/", escribir)

    def geo():
        for nombre, minimo in (("localidades.geojson", 19), ("region.geojson", 1)):
            d = json.loads((RAIZ / "app/static/geo" / nombre).read_text(encoding="utf-8"))
            n = len(d.get("features", []))
            (inf.ok if n >= minimo else inf.error)("archivos", f"Mapa {nombre}", f"{n} figuras",
                                                   *([] if n >= minimo else ["Vuelva a descomprimir el zip"]))
    _seguro(inf, "archivos", "Mapas", geo)

    # ---------------------------------------------------------------- configuración
    def config():
        from scraper.recolectar import cargar_conf
        conf = cargar_conf()
        inf.ok("config", "portales.yaml", "se lee bien")
        ua = str(conf["general"].get("user_agent", ""))
        if "cambiar" in ua.lower() or "@" not in ua:
            inf.aviso("config", "Correo de contacto", f"user_agent = {ua!r}",
                      "Ponga su correo real en general.user_agent de config/portales.yaml")
        else:
            inf.ok("config", "Correo de contacto", "puesto")
        autorizados = []
        for nombre, p in conf["portales"].items():
            if not p.get("habilitado"):
                inf.ok("config", f"Portal {nombre}", "deshabilitado (no se usa)")
            elif not p.get("terminos_revisados"):
                inf.aviso("config", f"Portal {nombre}", "habilitado pero sin terminos_revisados: true",
                          "Lea los términos del portal y ponga terminos_revisados: true y revisado_por: su nombre")
            else:
                autorizados.append(nombre)
                quien = str(p.get("revisado_por") or "")
                if not quien or "NOMBRE" in quien.upper():
                    inf.aviso("config", f"Portal {nombre}", f"autorizado, pero revisado_por = {quien!r}",
                              "Ponga en revisado_por el nombre real de quien leyó los términos")
                else:
                    inf.ok("config", f"Portal {nombre}", f"autorizado (revisado por {quien})")
        if not autorizados:
            inf.aviso("config", "Recolección automática", "ningún portal autorizado: no se recolectarán datos",
                      "Vea el punto anterior")
        auto = conf.get("automatico") or {}
        inf.ok("config", "Automatización", f"activo={auto.get('activo', True)}, cada "
                                           f"{auto.get('intervalo_horas', 12)} h")
        return conf
    _seguro(inf, "config", "portales.yaml", config)

    # ---------------------------------------------------------------- base de datos
    def base():
        from sqlalchemy import func, select

        from scraper import db
        ruta = RAIZ / "datos" / "vivienda.db"
        if not os.getenv("DATABASE_URL") and not ruta.exists():
            inf.aviso("datos", "Base vivienda.db", "no existe todavía",
                      "Si ya recolectó en otra carpeta, copie su vivienda.db a datos/. Si no, se creará sola.")
        eng = db.motor()
        with eng.begin() as c:
            cuenta = {t.name: c.scalar(select(func.count()).select_from(t)) for t in
                      (db.enlaces, db.inmuebles_crudos, db.inmuebles_limpios, db.cambios, db.ejecuciones)}
        inf.ok("datos", "Base de datos", ", ".join(f"{k}={v:,}" for k, v in cuenta.items()))
        from modelo import datos as D
        if cuenta["inmuebles_crudos"] and D.ubicar()[1] != "real":
            inf.aviso("datos", "Dataset real", f"la base tiene {cuenta['inmuebles_crudos']:,} avisos pero no hay "
                      "dataset_vN.csv en datos/", "Se construye solo al abrir la aplicación (iniciar.bat)")
        if cuenta["inmuebles_crudos"] == 0:
            inf.aviso("datos", "Avisos guardados", "la base no tiene avisos: la app usará datos de demostración",
                      "Copie su vivienda.db a datos/ o autorice un portal para que el ciclo recolecte")
    _seguro(inf, "datos", "Base de datos", base)

    def dataset():
        from modelo import datos as D
        ruta, tipo = D.ubicar()
        if ruta is None:
            inf.aviso("datos", "Dataset", "no hay ninguno: al abrir la app se crean datos de demostración")
            return
        df = D.cargar(ruta)
        (inf.aviso if tipo == "demo" else inf.ok)(
            "datos", "Dataset", f"{ruta.name} ({tipo}), {len(df):,} avisos, "
            f"{int(df[['latitud', 'longitud']].dropna().shape[0]):,} con coordenadas",
            *(["Son datos SINTÉTICOS: sirven para probar, no para el proyecto"] if tipo == "demo" else []))
        sin_loc = int(df["localidad"].isna().sum())
        if sin_loc:
            inf.aviso("datos", "Localidad", f"{sin_loc:,} avisos sin localidad (no salen en el mapa de zonas)")
    _seguro(inf, "datos", "Dataset", dataset)

    def modelo():
        from modelo import datos as D
        from modelo import entrenar, estimar
        art = estimar.cargar_modelo()
        if art is None:
            inf.aviso("modelo", "Modelo", "no hay modelo entrenado (o no se pudo cargar)",
                      "Se entrena solo al abrir la app; o: python -m modelo.entrenar")
            return
        ruta, _ = D.ubicar()
        vigente = (ruta is not None and art.get("sha256") == D.huella(ruta)
                   and art.get("version") == entrenar.VERSION_MODELO)
        (inf.ok if vigente else inf.aviso)("modelo", "Modelo", f"{art['nombre']}"
                                           + ("" if vigente else " (entrenado con otro dataset; se reentrena al abrir)"))
        m = json.loads(entrenar.METRICAS.read_text(encoding="utf-8"))
        p = m["prueba_final"][m["modelo_elegido"]]
        inf.ok("modelo", "Métricas", f"MAE {p['mae'] / 1e6:.1f} M, error relativo {p['error_relativo'] * 100:.1f} %, "
                                     f"entrenado {m['fecha']}")
        r = estimar.estimar(art, {"area_m2": 70, "estrato": 4, "localidad": None, "habitaciones": 2,
                                  "banos": 2, "parqueaderos": 1, "antiguedad": 10})
        inf.ok("modelo", "Estimación de prueba", f"70 m², estrato 4 -> ${r['precio']:,.0f}")
    _seguro(inf, "modelo", "Modelo", modelo)

    # ---------------------------------------------------------------- recolector en segundo plano
    def recolector():
        from scraper import servicio
        lat = servicio.leer_latido()
        if servicio.vivo(lat):
            inf.ok("recolector", "En segundo plano", f"vivo (pid {lat.get('pid')}, desde {lat.get('inicio')}, "
                   f"{lat.get('ciclos', 0)} ciclos; ahora: {lat.get('estado')} {lat.get('motivo') or ''})")
        elif lat:
            inf.aviso("recolector", "En segundo plano", f"no está corriendo (último latido {lat.get('latido')})",
                      "Se arranca solo al abrir la app; en Windows también al iniciar sesión (iniciar.bat lo configura)")
        else:
            inf.aviso("recolector", "En segundo plano", "nunca ha corrido en esta carpeta",
                      "Abra la app con iniciar.bat: lo arranca y deja configurado su inicio automático")
    _seguro(inf, "recolector", "En segundo plano", recolector)

    # ---------------------------------------------------------------- puerto y red
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        if s.connect_ex(("127.0.0.1", 8000)) == 0:
            inf.ok("red", "Puerto 8000", "ocupado: la aplicación (u otro programa) ya está abierta")
        else:
            inf.ok("red", "Puerto 8000", "libre")

    if con_red:
        def portales():
            import requests

            from scraper.recolectar import cargar_conf
            conf = cargar_conf()
            for nombre, p in conf["portales"].items():
                if not p.get("habilitado"):
                    continue
                t = time.perf_counter()
                try:
                    r = requests.get(p["base"].rstrip("/") + "/robots.txt", timeout=15,
                                     headers={"User-Agent": conf["general"]["user_agent"]})
                    ms = (time.perf_counter() - t) * 1000
                    if r.status_code == 200:
                        inf.ok("red", f"Conexión con {nombre}", f"robots.txt respondió en {ms:.0f} ms")
                    elif r.status_code in (403, 429):
                        inf.error("red", f"Conexión con {nombre}", f"respondió {r.status_code}: el portal está "
                                  "limitando el acceso desde esta red", "Espere unas horas antes de recolectar")
                    else:
                        inf.aviso("red", f"Conexión con {nombre}", f"respondió {r.status_code}")
                except requests.RequestException as e:
                    inf.error("red", f"Conexión con {nombre}", f"{type(e).__name__}: {e}",
                              "Revise internet, el proxy o el antivirus/firewall")
        _seguro(inf, "red", "Portales", portales)

    # ---------------------------------------------------------------- registros
    for ruta in (registro.GENERAL, registro.ERRORES):
        if ruta.exists():
            inf.ok("registros", ruta.name, f"{ruta.stat().st_size:,} bytes en {ruta.parent}")
    errores = errores_previos
    if errores:
        inf.aviso("registros", "Errores recientes", f"{len(errores)} en las últimas 200 líneas de errores.log; "
                  f"el último: {errores[-1][:300]}", "Abra datos/registros/errores.log")

    resumen = {e: sum(1 for i in inf.items if i["estado"] == e) for e in ("ok", "aviso", "error")}
    log.info("Diagnóstico terminado en %.1fs: %s", time.perf_counter() - t0, resumen)
    return {"fecha": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "resumen": resumen,
            "entorno": ent, "revisiones": inf.items}


def texto(r: dict) -> str:
    marcas = {"ok": "[ OK  ]", "aviso": "[AVISO]", "error": "[ERROR]"}
    lineas = [f"Diagnóstico de vivienda-bogota — {r['fecha']}",
              f"Python {r['entorno']['python']} | {r['entorno']['sistema']} | {r['entorno']['carpeta']}", ""]
    grupo = None
    for i in r["revisiones"]:
        if i["grupo"] != grupo:
            grupo = i["grupo"]
            lineas.append(f"\n== {grupo.upper()} ==")
        lineas.append(f"{marcas[i['estado']]} {i['revision']}: {i['detalle']}")
        if i["arreglo"]:
            lineas.append(f"         Cómo arreglarlo: {i['arreglo']}")
    s = r["resumen"]
    lineas += ["", f"Resultado: {s['ok']} bien, {s['aviso']} avisos, {s['error']} errores."]
    if s["error"]:
        lineas.append("Arregle primero los [ERROR]. Si no sabe cómo, envíe este archivo.")
    return "\n".join(lineas)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sin-red", action="store_true", help="no probar la conexión con los portales")
    a = ap.parse_args(argv)
    registro.configurar("diagnostico", nivel=logging.INFO, consola=False)   # todo al archivo; en pantalla, el informe
    r = revisar(con_red=not a.sin_red)
    salida = texto(r)
    print(salida)
    destino = registro.CARPETA / f"diagnostico-{dt.datetime.now():%Y%m%d-%H%M%S}.txt"
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(salida, encoding="utf-8")
        print(f"\nInforme guardado en {destino}")
    except OSError as e:
        print(f"\nNo se pudo guardar el informe: {e}")
    sys.exit(1 if r["resumen"]["error"] else 0)


if __name__ == "__main__":
    main()
