"""Recolección en dos fases, tal como la describe el documento del proyecto (sección 9.1).

Uso:
  python -m scraper.recolectar diagnostico --portal fincaraiz --url <URL de un aviso>
  python -m scraper.recolectar listado     --portal fincaraiz --paginas 10
  python -m scraper.recolectar detalle     --portal fincaraiz --max 200
  python -m scraper.recolectar estado

Siempre empezar por `diagnostico` sobre 2 o 3 avisos para confirmar que los campos salen bien.
"""
import argparse
import datetime as dt
import logging
import pathlib
import sys

import yaml
from sqlalchemy import func, insert, select, update

from . import db
from .extraer import CAMPOS, enlaces_detalle, extraer
from .http import BloqueoDelPortal, Cliente

RAIZ = pathlib.Path(__file__).resolve().parents[1]


def cargar_conf():
    ruta = RAIZ / "config" / "portales.yaml"
    try:
        conf = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logging.getLogger("vivienda.config").error("No existe %s: restáurelo desde el zip o el repositorio", ruta)
        raise
    except UnicodeDecodeError:
        logging.getLogger("vivienda.config").error(
            "%s no está guardado en UTF-8 (pasa al editarlo con el Bloc de notas antiguo). "
            "Ábralo y use Guardar como > Codificación UTF-8", ruta)
        raise
    except yaml.YAMLError as e:
        marca = getattr(e, "problem_mark", None)
        donde = f" en la línea {marca.line + 1}, columna {marca.column + 1}" if marca else ""
        logging.getLogger("vivienda.config").error(
            "config/portales.yaml tiene un error de formato%s: %s. Revise la sangría (solo espacios, no tabulaciones) "
            "y las comillas.", donde, getattr(e, "problem", e))
        raise
    if not isinstance(conf, dict) or "portales" not in conf or "general" not in conf:
        logging.getLogger("vivienda.config").error("config/portales.yaml no tiene las secciones 'general' y 'portales'")
        raise ValueError("config/portales.yaml incompleto")
    return conf


def portal_autorizado(conf, nombre):
    p = conf["portales"].get(nombre)
    if not p:
        sys.exit(f"Portal desconocido: {nombre}")
    if not p.get("habilitado"):
        sys.exit(f"{nombre} está deshabilitado en config/portales.yaml. Motivo anotado allí.")
    if not p.get("terminos_revisados"):
        sys.exit(f"{nombre}: falta revisar los términos de uso. Léanlos y, si permiten la "
                 f"extracción, pongan terminos_revisados: true, revisado_por y fecha_revision.")
    return p


def cmd_diagnostico(args, conf):
    p = portal_autorizado(conf, args.portal)
    cli = Cliente(conf["general"])
    html = cli.get(args.url, p["base"])
    carpeta = RAIZ / "datos" / "diagnostico"
    carpeta.mkdir(parents=True, exist_ok=True)
    archivo = carpeta / (args.url.rstrip("/").split("/")[-1][:80] + ".html")
    archivo.write_text(html, encoding="utf-8")
    campos, metodo = extraer(html, args.url, p.get("patron_barrio_url"))
    print(f"HTML guardado en {archivo} (solo para revisión local; no subirlo al repositorio)")
    print(f"Método: {metodo}")
    for k in CAMPOS:
        print(f"  {k:15s} {campos.get(k, '— no encontrado —')}")
    print("\nCompare estos valores con lo que muestra el aviso en el navegador.")


def cmd_diagnostico_listado(args, conf):
    p = portal_autorizado(conf, args.portal)
    cli = Cliente(conf["general"])
    url = p["listado_pagina_1"] if args.pagina == 1 else p["listado_pagina_n"].format(pagina=args.pagina)
    html = cli.get(url, p["base"])
    carpeta = RAIZ / "datos" / "diagnostico"
    carpeta.mkdir(parents=True, exist_ok=True)
    archivo = carpeta / f"listado_{args.portal}_p{args.pagina}.html"
    archivo.write_text(html, encoding="utf-8")
    links = enlaces_detalle(html, p["base"], p["patron_detalle"])
    print(f"URL: {url}")
    print(f"Tamaño de la página: {len(html):,} caracteres")
    print(f"HTML guardado en {archivo}")
    print(f"Avisos encontrados: {len(links)}")
    for u in links[:5]:
        print("  ", u)


def cmd_listado(args, conf):
    p = portal_autorizado(conf, args.portal)
    cli, eng = Cliente(conf["general"]), db.motor()
    paginas = min(args.paginas, conf["general"]["max_paginas_listado"])
    nuevos, vacias = 0, 0
    max_vacias = conf["general"].get("max_paginas_vacias_seguidas", 5)
    for n in range(args.desde, args.desde + paginas):
        url = p["listado_pagina_1"] if n == 1 else p["listado_pagina_n"].format(pagina=n)
        try:
            html = cli.get(url, p["base"])
        except BloqueoDelPortal as e:
            print(f"DETENIDO: {e}. No reintentar hoy; revisar si el portal limita el acceso.")
            break
        except PermissionError as e:
            print(f"OMITIDO: {e}")
            break
        links = enlaces_detalle(html, p["base"], p["patron_detalle"])
        if not links:
            # Las primeras páginas pueden traer solo proyectos destacados (excluidos a propósito).
            # Solo se asume el fin del listado tras varias páginas seguidas sin avisos útiles.
            vacias += 1
            print(f"Página {n}: 0 avisos individuales (solo proyectos u otros). "
                  f"Vacías seguidas: {vacias}/{max_vacias}")
            if vacias >= max_vacias:
                print("Se detiene: fin del listado o el patrón de enlaces cambió.")
                break
            continue
        vacias = 0
        with eng.begin() as c:
            existentes = set(c.scalars(select(db.enlaces.c.url).where(db.enlaces.c.url.in_(links))))
            filas = [{"url": u, "portal": args.portal, "procesado": False}
                     for u in links if u not in existentes]
            if filas:
                c.execute(insert(db.enlaces), filas)
        nuevos += len(filas)
        print(f"Página {n}: {len(links)} avisos, {len(filas)} nuevos")
    print(f"Total de enlaces nuevos: {nuevos}")


def cmd_detalle(args, conf):
    p = portal_autorizado(conf, args.portal)
    cli, eng = Cliente(conf["general"]), db.motor()
    limite = min(args.max, conf["general"]["max_avisos_por_ejecucion"])
    with eng.begin() as c:
        pendientes = list(c.scalars(
            select(db.enlaces.c.url).where(db.enlaces.c.portal == args.portal,
                                          db.enlaces.c.procesado.is_(False)).limit(limite)))
    print(f"{len(pendientes)} avisos por visitar")
    ok = 0
    for i, url in enumerate(pendientes, 1):
        estado = "ok"
        try:
            html = cli.get(url, p["base"])
            campos, metodo = extraer(html, url, p.get("patron_barrio_url"))
            fila = {f"{k}_txt": campos.get(k) for k in CAMPOS}
            fila.update(portal=args.portal, url_origen=url, fecha_consulta=dt.date.today(),
                        metodo_extraccion=metodo)
            with eng.begin() as c:
                ya = c.scalar(select(db.inmuebles_crudos.c.id)
                              .where(db.inmuebles_crudos.c.url_origen == url))
                if ya is None:
                    c.execute(insert(db.inmuebles_crudos), fila)
            ok += 1
        except BloqueoDelPortal as e:
            print(f"DETENIDO en el aviso {i}: {e}")
            break
        except PermissionError:
            estado = "robots"
        except Exception as e:  # un aviso dañado no debe tumbar la ejecución
            estado = f"error:{type(e).__name__}"[:40]
        with eng.begin() as c:
            c.execute(update(db.enlaces).where(db.enlaces.c.url == url)
                      .values(procesado=True, ultimo_estado=estado))
        if i % 25 == 0:
            print(f"  {i}/{len(pendientes)} procesados, {ok} guardados")
    print(f"Guardados en inmuebles_crudos: {ok}")


def cmd_estado(args, conf):
    eng = db.motor()
    with eng.begin() as c:
        for portal, total, hechos in c.execute(
                select(db.enlaces.c.portal, func.count(),
                       func.sum(db.enlaces.c.procesado.cast(db.Integer)))
                .group_by(db.enlaces.c.portal)):
            print(f"{portal:12s} enlaces={total} procesados={hechos or 0}")
        print("crudos:", c.scalar(select(func.count()).select_from(db.inmuebles_crudos)))
        print("limpios:", c.scalar(select(func.count()).select_from(db.inmuebles_limpios)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("diagnostico"); d.add_argument("--portal", required=True); d.add_argument("--url", required=True)
    dl = sub.add_parser("diagnostico-listado"); dl.add_argument("--portal", required=True)
    dl.add_argument("--pagina", type=int, default=1)
    l = sub.add_parser("listado"); l.add_argument("--portal", required=True)
    l.add_argument("--paginas", type=int, default=5); l.add_argument("--desde", type=int, default=1)
    t = sub.add_parser("detalle"); t.add_argument("--portal", required=True); t.add_argument("--max", type=int, default=100)
    sub.add_parser("estado")
    args = ap.parse_args(argv)
    from comun import registro
    registro.configurar("recolectar")
    logging.getLogger("vivienda.recolectar").info("Comando: %s", vars(args))
    conf = cargar_conf()
    {"diagnostico": cmd_diagnostico, "diagnostico-listado": cmd_diagnostico_listado,
     "listado": cmd_listado,
     "detalle": cmd_detalle, "estado": cmd_estado}[args.cmd](args, conf)


if __name__ == "__main__":
    main()
