"""Historial: lo que la base sabe del pasado y ningún portal muestra.

  - historia(url): la línea de tiempo de un aviso (primera vez visto, cada cambio de precio o de
    datos, retiro, última verificación) y cuántos días lleva publicado;
  - tendencia(df, localidad): m² mediano de los avisos que aparecieron cada semana, cambios de
    precio de los últimos 30 días y cuánto duran publicados los que ya se retiraron.

Todo sale de las tablas inmuebles_crudos y cambios. Con pocas semanas de recolección la tendencia
se marca como insuficiente en vez de dibujar una línea engañosa.
"""
from __future__ import annotations

import datetime as dt
import logging

import numpy as np
import pandas as pd
from sqlalchemy import select

from limpieza import normalizar as nz
from scraper import db

log = logging.getLogger("vivienda.historial")
SEMANAS_MINIMAS = 3
AVISOS_POR_SEMANA = 5


def _fecha(v):
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NaT:
        return None
    return v.isoformat(timespec="seconds") if isinstance(v, dt.datetime) else str(v)


def historia(eng, url: str) -> dict | None:
    t, c = db.inmuebles_crudos.c, db.cambios.c
    with eng.begin() as cx:
        a = cx.execute(select(t.url_origen, t.portal, t.precio_txt, t.primera_vez, t.verificado_en, t.actualizado_en,
                              t.publicado_txt, t.disponible, t.retirado_en, t.fecha_consulta)
                       .where(t.url_origen == url)).mappings().first()
        if a is None:
            return None
        cambios = cx.execute(select(c.fecha, c.campo, c.antes, c.despues).where(c.url_origen == url)
                             .order_by(c.fecha)).mappings().all()
    eventos = []
    inicio = a["primera_vez"] or (dt.datetime.combine(a["fecha_consulta"], dt.time()) if a["fecha_consulta"] else None)
    publicado = nz.fecha(a["publicado_txt"])
    if publicado:
        eventos.append({"fecha": publicado.isoformat(), "tipo": "publicado", "texto": "Publicado en el portal"})
    if inicio:
        eventos.append({"fecha": _fecha(inicio), "tipo": "visto", "texto": "La aplicación lo encontró"})
    for x in cambios:
        ev = {"fecha": _fecha(x["fecha"]), "tipo": x["campo"], "antes": x["antes"], "despues": x["despues"]}
        if x["campo"] == "precio":
            antes, despues = nz.precio(x["antes"]), nz.precio(x["despues"])
            if antes and despues:
                ev["variacion"] = round(despues / antes - 1, 4)
                ev["texto"] = f"Precio: {'bajó' if despues < antes else 'subió'} {abs(despues / antes - 1) * 100:.1f} %"
        elif x["campo"] == "disponible":
            ev["texto"] = "Retirado del portal" if x["despues"] == "no" else "Volvió a publicarse"
        else:
            ev["texto"] = f"Cambió {x['campo']}: {x['antes'] or '—'} → {x['despues'] or '—'}"
        eventos.append(ev)
    if a["verificado_en"]:
        eventos.append({"fecha": _fecha(a["verificado_en"]), "tipo": "verificado", "texto": "Última verificación con la fuente"})
    desde = dt.datetime.combine(publicado, dt.time()) if publicado else inicio
    hasta = a["retirado_en"] if a["disponible"] is False and a["retirado_en"] else dt.datetime.now()
    precios = [{"fecha": e["fecha"], "precio": nz.precio(e["despues"])} for e in eventos if e["tipo"] == "precio"]
    if cambios and any(x["campo"] == "precio" for x in cambios):
        primero = next(x for x in cambios if x["campo"] == "precio")
        precios.insert(0, {"fecha": _fecha(inicio), "precio": nz.precio(primero["antes"])})
    return {"url": url, "portal": a["portal"], "disponible": a["disponible"] is not False,
            "dias_publicado": (hasta - desde).days if desde else None,
            "desde_fuente": "publicación en el portal" if publicado else "primera vez que la aplicación lo vio",
            "eventos": sorted(eventos, key=lambda e: e["fecha"] or ""), "precios": precios}


def tendencia(eng, df: pd.DataFrame, localidad: str | None) -> dict:
    """Semana a semana, a partir de los avisos útiles del dataset (sin atípicos) y sus fechas en la base."""
    t = db.inmuebles_crudos.c
    with eng.begin() as cx:
        fechas = pd.DataFrame(cx.execute(select(t.url_origen, t.primera_vez, t.fecha_consulta, t.disponible,
                                                t.retirado_en, t.publicado_txt)).all(),
                              columns=["url_origen", "primera_vez", "fecha_consulta", "disponible", "retirado_en",
                                       "publicado_txt"])
        cambios = pd.DataFrame(cx.execute(select(db.cambios.c.url_origen, db.cambios.c.fecha, db.cambios.c.antes,
                                                 db.cambios.c.despues).where(db.cambios.c.campo == "precio")).all(),
                               columns=["url_origen", "fecha", "antes", "despues"])
    g = df if not localidad else df[df["localidad"] == localidad]
    zona = localidad or "Bogotá"
    if g.empty or fechas.empty:
        return {"zona": zona, "suficiente": False, "semanas": [], "mensaje": "Sin avisos en esta zona."}
    m = g[["url_origen", "precio_m2"]].merge(fechas, on="url_origen", how="left")
    m["visto"] = pd.to_datetime(m["primera_vez"]).fillna(pd.to_datetime(m["fecha_consulta"]))
    m = m.dropna(subset=["visto"])
    m["semana"] = m["visto"].dt.to_period("W-SUN").dt.start_time
    sem = (m.groupby("semana").agg(n=("precio_m2", "size"), mediana_m2=("precio_m2", "median"))
           .reset_index().sort_values("semana"))
    validas = sem[sem["n"] >= AVISOS_POR_SEMANA]
    semanas = [{"semana": r.semana.date().isoformat(), "n": int(r.n), "mediana_m2": float(r.mediana_m2),
                "suficiente": bool(r.n >= AVISOS_POR_SEMANA)} for r in sem.itertuples()]
    suficiente = len(validas) >= SEMANAS_MINIMAS
    variacion = (float(validas["mediana_m2"].iloc[-1] / validas["mediana_m2"].iloc[0] - 1) if suficiente else None)
    # Cambios de precio de los avisos de la zona en los últimos 30 días
    hace30 = dt.datetime.now() - dt.timedelta(days=30)
    cz = cambios[cambios["url_origen"].isin(g["url_origen"]) & (pd.to_datetime(cambios["fecha"]) >= hace30)].copy()
    cz["var"] = [(nz.precio(d) or np.nan) / (nz.precio(a) or np.nan) - 1 for a, d in zip(cz["antes"], cz["despues"], strict=True)]
    cz = cz.dropna(subset=["var"])
    # Duración de los avisos ya retirados: desde la publicación (o la primera vez visto) hasta el retiro
    ret = m[(m["disponible"] == False) & m["retirado_en"].notna()].copy()  # noqa: E712
    if len(ret):
        pub = pd.to_datetime([nz.fecha(x) for x in ret["publicado_txt"]], errors="coerce")
        inicio = pd.Series(pub, index=ret.index).fillna(ret["visto"])
        ret["dias"] = (pd.to_datetime(ret["retirado_en"]) - inicio).dt.days
    return {"zona": zona, "suficiente": suficiente, "semanas": semanas, "variacion_m2": variacion,
            "minimo_semanas": SEMANAS_MINIMAS, "minimo_por_semana": AVISOS_POR_SEMANA,
            "cambios_30_dias": {"n": int(len(cz)), "bajadas": int((cz["var"] < 0).sum()), "subidas": int((cz["var"] > 0).sum()),
                                "variacion_mediana": float(cz["var"].median()) if len(cz) else None},
            "retirados": {"n": int(len(ret)), "dias_mediana": float(ret["dias"].median()) if len(ret) else None}}
