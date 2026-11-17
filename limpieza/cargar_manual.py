"""Plan de contingencia: carga de avisos capturados a mano (si un portal no permite la extracción
automatizada o bloquea el acceso). Los registros entran a inmuebles_crudos igual que los del
scraper, de modo que pasan por las mismas reglas de limpieza.

Uso:
  python -m limpieza.cargar_manual carga_manual/plantilla_captura.csv
"""
import datetime as dt
import sys

import pandas as pd
from sqlalchemy import insert, select

from scraper import db
from scraper.extraer import CAMPOS


def main(ruta):
    try:
        df = pd.read_csv(ruta, dtype=str, encoding="utf-8-sig").fillna("")
    except UnicodeDecodeError:  # CSV guardado por Excel en Windows
        df = pd.read_csv(ruta, dtype=str, encoding="cp1252").fillna("")
    for opcional in ("piso", "estado", "latitud", "longitud"):
        if opcional not in df.columns:
            df[opcional] = ""
    faltan = {"url_origen", "fecha_consulta", *CAMPOS} - set(df.columns)
    if faltan:
        sys.exit(f"Faltan columnas: {sorted(faltan)}")
    eng, n = db.motor(), 0
    with eng.begin() as c:
        for r in df.to_dict("records"):
            if not r["url_origen"] or c.scalar(select(db.inmuebles_crudos.c.id)
                                               .where(db.inmuebles_crudos.c.url_origen == r["url_origen"])):
                continue
            fila = {f"{k}_txt": (r[k] or None) for k in CAMPOS}
            fila.update(portal=r.get("portal") or "manual", url_origen=r["url_origen"],
                        fecha_consulta=dt.date.fromisoformat(r["fecha_consulta"]),
                        metodo_extraccion="manual")
            c.execute(insert(db.inmuebles_crudos), fila)
            n += 1
    print(f"{n} avisos cargados")


if __name__ == "__main__":
    main(sys.argv[1])
