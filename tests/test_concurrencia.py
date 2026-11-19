"""Regresión: la app, el recolector y su ciclo escriben y leen la misma base a la vez sin
'database is locked' (pasaba con SQLite en su modo por defecto)."""
import datetime as dt
import multiprocessing as mp

from sqlalchemy import func, select, text, update


def _escritor(url, prefijo, n, errores):
    import os
    os.environ["DATABASE_URL"] = url
    from scraper import db
    eng = db.motor()
    try:
        for i in range(n):
            with eng.begin() as c:     # una transacción por aviso, como el ciclo
                c.execute(db.inmuebles_crudos.insert(), {
                    "portal": "fincaraiz", "url_origen": f"https://x.co/{prefijo}/{i}", "precio_txt": "1",
                    "fecha_consulta": dt.date.today(), "metodo_extraccion": "prueba"})
                c.execute(update(db.inmuebles_crudos).where(db.inmuebles_crudos.c.url_origen.like(f"%/{prefijo}/%"))
                          .values(verificado_en=dt.datetime.now()))
    except Exception as e:  # noqa: BLE001
        errores.append(f"{prefijo}: {e}")


def _lector(url, segundos, errores):
    import os
    import time
    os.environ["DATABASE_URL"] = url
    from scraper import db
    eng = db.motor()
    fin = time.time() + segundos
    try:
        while time.time() < fin:
            with eng.begin() as c:
                c.execute(select(db.inmuebles_crudos.c.url_origen, db.inmuebles_crudos.c.verificado_en)).all()
    except Exception as e:  # noqa: BLE001
        errores.append(f"lector: {e}")


def test_escrituras_simultaneas_sin_bloqueos(tmp_path):
    url = f"sqlite:///{tmp_path}/c.db"
    import os
    os.environ["DATABASE_URL"] = url
    from scraper import db
    eng = db.motor()
    with eng.begin() as c:
        assert c.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    ctx = mp.get_context("spawn")
    with ctx.Manager() as m:
        errores = m.list()
        procesos = [ctx.Process(target=_escritor, args=(url, p, 150, errores)) for p in ("app", "recolector", "ciclo")]
        procesos.append(ctx.Process(target=_lector, args=(url, 4, errores)))
        for p in procesos:
            p.start()
        for p in procesos:
            p.join(120)
        assert list(errores) == []
    with eng.begin() as c:
        assert c.scalar(select(func.count()).select_from(db.inmuebles_crudos)) == 450
