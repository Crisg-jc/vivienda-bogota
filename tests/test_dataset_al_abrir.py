"""Con avisos en la base y sin dataset_vN.csv, la app construye el dataset real al abrir."""
import datetime as dt
import importlib

import pytest
from fastapi.testclient import TestClient


def test_app_construye_dataset_real_desde_la_base(tmp_path):
    mp = pytest.MonkeyPatch()
    try:
        mp.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/v.db")
        mp.setenv("VIVIENDA_AUTOMATICO", "0")
        mp.delenv("VIVIENDA_DATASET", raising=False)
        from limpieza import construir_dataset as cd
        from modelo import datos as D
        from modelo import demo, entrenar, estimar
        from scraper import db
        mp.setattr(cd, "RAIZ", tmp_path)
        mp.setattr(D, "RAIZ", tmp_path)
        mp.setattr(D, "DEMO", tmp_path / "datos" / "demo" / "dataset_demo.csv")
        mp.setattr(demo, "DEMO", D.DEMO)
        for nombre, valor in (("ARTEFACTOS", tmp_path / "a"), ("MODELO", tmp_path / "a" / "m.joblib"),
                              ("METRICAS", tmp_path / "a" / "metricas.json")):
            mp.setattr(entrenar, nombre, valor)
        mp.setattr(estimar, "MODELO", entrenar.MODELO)
        eng = db.motor()
        filas = [dict(portal="fincaraiz", url_origen=f"u{i}", precio_txt=str(300_000_000 + i * 9_000_000),
                      area_txt=str(50 + i * 2), habitaciones_txt="3", banos_txt="2", estrato_txt=str(2 + i % 4),
                      parqueaderos_txt="1", localidad_txt=["Suba", "Kennedy", "Chapinero"][i % 3],
                      barrio_txt=None, tipo_txt="Apartamento", metodo_extraccion="test",
                      fecha_consulta=dt.date(2026, 11, 1)) for i in range(30)]
        with eng.begin() as c:
            c.execute(db.inmuebles_crudos.insert(), filas)
        import app.main as principal
        importlib.reload(principal)
        with TestClient(principal.app) as c:
            estado = c.get("/api/estado").json()
        assert estado["dataset"]["tipo"] == "real"
        assert estado["dataset"]["archivo"] == "dataset_v1.csv"
        assert estado["dataset"]["avisos"] == 30
    finally:
        mp.undo()
