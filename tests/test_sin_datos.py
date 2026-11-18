"""Sin datos reales la app no inventa nada; con datos, cada aviso trae su trazabilidad en vivo."""
import datetime as dt
import importlib

import pytest
from fastapi.testclient import TestClient


def preparar(tmp_path, mp):
    mp.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/v.db")
    mp.setenv("VIVIENDA_AUTOMATICO", "0")
    mp.delenv("VIVIENDA_DATASET", raising=False)
    mp.delenv("VIVIENDA_DEMO", raising=False)
    from limpieza import construir_dataset as cd
    from modelo import datos as D
    from modelo import demo, entrenar, estimar
    mp.setattr(cd, "RAIZ", tmp_path)
    mp.setattr(D, "RAIZ", tmp_path)
    mp.setattr(D, "DEMO", tmp_path / "datos" / "demo" / "dataset_demo.csv")
    mp.setattr(demo, "DEMO", D.DEMO)
    for nombre, valor in (("ARTEFACTOS", tmp_path / "a"), ("MODELO", tmp_path / "a" / "m.joblib"),
                          ("METRICAS", tmp_path / "a" / "metricas.json")):
        mp.setattr(entrenar, nombre, valor)
    mp.setattr(estimar, "MODELO", entrenar.MODELO)
    import app.main as principal
    importlib.reload(principal)
    return principal


def test_sin_avisos_reales_no_hay_datos_inventados(tmp_path):
    mp = pytest.MonkeyPatch()
    try:
        principal = preparar(tmp_path, mp)
        with TestClient(principal.app) as c:
            e = c.get("/api/estado").json()
            assert e["dataset"]["tipo"] == "ninguno" and e["dataset"]["avisos"] == 0
            assert c.get("/api/salud").json()["estado"] == "sin_datos"
            r = c.post("/api/estimar", json={"area_m2": 70, "estrato": 4})
            assert r.status_code == 503 and "aviso real" in r.json()["detail"]
            for ruta in ("/api/mercado", "/api/zonas", "/api/avisos", "/api/modelo"):
                assert c.get(ruta).status_code == 503, ruta
        assert not (tmp_path / "datos" / "demo").exists(), "no se crean datos sintéticos"
    finally:
        mp.undo()


def test_cada_aviso_trae_fuente_y_fechas_y_los_retirados_no_se_muestran(tmp_path):
    mp = pytest.MonkeyPatch()
    try:
        from scraper import db
        mp.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/v.db")
        eng = db.motor()
        hoy = dt.datetime.now().replace(microsecond=0)
        filas = [dict(portal="fincaraiz", url_origen=f"https://x.co/apto/{i}", precio_txt=str(300_000_000 + i * 9_000_000),
                      area_txt=str(50 + i * 2), habitaciones_txt="3", banos_txt="2", estrato_txt=str(2 + i % 4),
                      localidad_txt=["Suba", "Kennedy", "Chapinero"][i % 3], tipo_txt="Apartamento",
                      metodo_extraccion="test", fecha_consulta=hoy.date(), publicado_txt="2026-10-01",
                      primera_vez=hoy - dt.timedelta(days=20), verificado_en=hoy - dt.timedelta(hours=2),
                      actualizado_en=hoy - dt.timedelta(days=5), disponible=True) for i in range(30)]
        filas[0]["verificado_en"] = hoy - dt.timedelta(days=30)      # sin verificar hace un mes
        with eng.begin() as c:
            c.execute(db.inmuebles_crudos.insert(), filas)
        principal = preparar(tmp_path, mp)
        with TestClient(principal.app) as c:
            estado = c.get("/api/estado").json()
            assert estado["dataset"]["tipo"] == "real"
            v = estado["verificacion"]
            assert v["registros"] == 30 and v["vigentes"] == 29 and v["sin_verificar_reciente"] == 1
            avisos = c.get("/api/avisos?por_pagina=100").json()["avisos"]
            a = next(x for x in avisos if x["url_origen"].endswith("/5"))
            assert a["fuente"]["portal"] == "fincaraiz" and a["fuente"]["publicado"] == "2026-10-01"
            assert a["fuente"]["vigente"] is True and a["fuente"]["verificado_en"]
            viejo = next(x for x in avisos if x["url_origen"].endswith("/0"))
            assert viejo["fuente"]["vigente"] is False
            # El portal retira un aviso: deja de mostrarse en seguida, sin esperar un dataset nuevo.
            with eng.begin() as cx:
                cx.execute(db.inmuebles_crudos.update().where(db.inmuebles_crudos.c.url_origen.like("%/7"))
                           .values(disponible=False))
            principal.actualizar_traza()
            urls = {x["url_origen"] for x in c.get("/api/avisos?por_pagina=100").json()["avisos"]}
            assert "https://x.co/apto/7" not in urls and len(urls) == 29
    finally:
        mp.undo()
