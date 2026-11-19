"""Historial: línea de tiempo de un aviso, tendencia semanal y duración de los retirados."""
import datetime as dt

import pandas as pd

from app import historial
from scraper import db


def _base(tmp_path, monkeypatch, semanas=4):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/h.db")
    eng = db.motor()
    hoy = dt.datetime.now().replace(microsecond=0)
    filas, df = [], []
    for s in range(semanas):
        for i in range(6):
            url = f"https://x.co/a/{s}-{i}"
            visto = hoy - dt.timedelta(weeks=semanas - s, hours=i)
            filas.append(dict(portal="fincaraiz", url_origen=url, precio_txt="400000000", fecha_consulta=visto.date(),
                              metodo_extraccion="t", primera_vez=visto, verificado_en=hoy, disponible=True,
                              publicado_txt=(visto - dt.timedelta(days=3)).date().isoformat(), retirado_en=None))
            df.append({"url_origen": url, "localidad": "Suba", "precio_m2": 5_000_000 + s * 100_000})
    filas[0].update(disponible=False, retirado_en=filas[0]["primera_vez"] + dt.timedelta(days=20))
    with eng.begin() as c:
        c.execute(db.inmuebles_crudos.insert(), filas)
        c.execute(db.cambios.insert(), [
            {"url_origen": filas[1]["url_origen"], "fecha": hoy - dt.timedelta(days=5), "campo": "precio",
             "antes": "400000000", "despues": "380000000"},
            {"url_origen": filas[0]["url_origen"], "fecha": filas[0]["retirado_en"], "campo": "disponible",
             "antes": "sí", "despues": "no"}])
    return eng, pd.DataFrame(df), filas


def test_linea_de_tiempo_de_un_aviso(tmp_path, monkeypatch):
    eng, _, filas = _base(tmp_path, monkeypatch)
    h = historial.historia(eng, filas[1]["url_origen"])
    tipos = [e["tipo"] for e in h["eventos"]]
    assert tipos[:2] == ["publicado", "visto"] and "precio" in tipos and tipos[-1] == "verificado"
    bajada = next(e for e in h["eventos"] if e["tipo"] == "precio")
    assert bajada["variacion"] == -0.05 and "bajó 5.0 %" in bajada["texto"]
    assert h["precios"][0]["precio"] == 400_000_000 and h["precios"][-1]["precio"] == 380_000_000
    assert h["dias_publicado"] >= 3 and h["desde_fuente"] == "publicación en el portal"
    r = historial.historia(eng, filas[0]["url_origen"])
    assert not r["disponible"] and r["dias_publicado"] == 23
    assert historial.historia(eng, "https://no.existe") is None


def test_tendencia_semanal_cambios_y_duracion(tmp_path, monkeypatch):
    eng, df, _ = _base(tmp_path, monkeypatch)
    t = historial.tendencia(eng, df, "Suba")
    assert t["suficiente"] and len([s for s in t["semanas"] if s["suficiente"]]) >= 3
    assert t["variacion_m2"] > 0
    assert t["cambios_30_dias"]["bajadas"] == 1 and t["retirados"]["n"] == 1 and t["retirados"]["dias_mediana"] == 23


def test_sin_semanas_suficientes_no_hay_tendencia(tmp_path, monkeypatch):
    eng, df, _ = _base(tmp_path, monkeypatch, semanas=2)
    t = historial.tendencia(eng, df, "Suba")
    assert not t["suficiente"] and t["variacion_m2"] is None
