"""Entrena y compara los modelos del proyecto y guarda el elegido.

Uso:
  python -m modelo.entrenar                 # usa el dataset que encuentre modelo.datos.ubicar()
  python -m modelo.entrenar --dataset datos/dataset_v3.csv

Lo que hace, en orden:
  1. separa el 20 % de los avisos como prueba final; el modelo no los ve durante la selección;
  2. compara con validación cruzada (5 partes) la línea base y tres modelos de árboles;
  3. elige el de menor error absoluto medio (MAE). Si ninguno supera a la línea base, se queda
     con la línea base y lo dice en las métricas, en vez de esconderlo;
  4. mide el elegido una sola vez en la prueba final, incluida la cobertura del rango de precio;
  5. lo vuelve a entrenar con todos los datos y lo guarda en artefactos/ junto con metricas.json.

Los árboles aprenden log(precio): así los apartamentos más caros no dominan el aprendizaje.
Si MLflow está instalado, cada entrenamiento queda registrado en mlruns/.
"""
import argparse
import datetime as dt
import json
import logging
import math
import pathlib
import time

import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.compose import ColumnTransformer, TransformedTargetRegressor
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import KFold, cross_val_predict, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.tree import DecisionTreeRegressor

from . import datos as D
from .linea_base import LineaBase

log = logging.getLogger("vivienda.modelo")

ARTEFACTOS = D.RAIZ / "artefactos"
MODELO = ARTEFACTOS / "modelo.joblib"
METRICAS = ARTEFACTOS / "metricas.json"
SEMILLA = 42
NIVEL_RANGO = 0.80          # el rango de precio debe contener el valor real 8 de cada 10 veces
MINIMO_AVISOS = 12


def preparar(df: pd.DataFrame) -> pd.DataFrame:
    X = pd.DataFrame({c: pd.to_numeric(df.get(c), errors="coerce").astype(float) for c in D.NUMERICAS})
    X["localidad"] = df.get("localidad").astype(object).where(df.get("localidad").notna(), "Sin dato").astype(str)
    return X


def arbol(regresor) -> TransformedTargetRegressor:
    pre = ColumnTransformer([
        ("num", SimpleImputer(strategy="median", keep_empty_features=True), D.NUMERICAS),
        ("loc", OneHotEncoder(handle_unknown="ignore", sparse_output=False), D.CATEGORICAS),
    ])
    return TransformedTargetRegressor(Pipeline([("pre", pre), ("reg", regresor)]),
                                      func=np.log, inverse_func=np.exp)


def candidatos(n: int) -> dict:
    hoja = max(2, min(10, n // 20))   # con pocos datos, hojas más pequeñas
    return {
        "Línea base": LineaBase(),
        "Árbol de decisión": arbol(DecisionTreeRegressor(max_depth=6, min_samples_leaf=hoja, random_state=SEMILLA)),
        "Bosque aleatorio": arbol(RandomForestRegressor(n_estimators=300, min_samples_leaf=max(1, hoja // 2),
                                                        random_state=SEMILLA, n_jobs=-1)),
        "Gradient boosting": arbol(HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05,
                                                                 min_samples_leaf=hoja, random_state=SEMILLA)),
    }


def medir(y, pred) -> dict:
    y, pred = np.asarray(y, float), np.asarray(pred, float)
    mae = mean_absolute_error(y, pred)
    return {"mae": round(mae), "rmse": round(math.sqrt(mean_squared_error(y, pred))),
            "r2": round(float(r2_score(y, pred)), 3) if len(y) > 1 else None,
            "error_relativo": round(mae / float(np.median(y)), 4)}


def pliegues(n: int) -> KFold:
    return KFold(n_splits=max(2, min(5, n // 3)), shuffle=True, random_state=SEMILLA)


def cuantil_conformal(modelo, X, y, con_predicciones: bool = False):
    """Ancho del rango en escala logarítmica, calculado con predicciones fuera de muestra.

    Con con_predicciones=True devuelve también esas predicciones: el precio que el modelo esperaba
    para cada aviso sin haberlo visto al entrenar. La app las usa para decir si un aviso está por
    encima o por debajo de lo esperado sin hacerse trampa con avisos memorizados."""
    oof = cross_val_predict(clone(modelo), X, y, cv=pliegues(len(y)))
    r = np.sort(np.abs(np.log(y) - np.log(np.clip(oof, 1, None))))
    k = min(len(r) - 1, math.ceil((len(r) + 1) * NIVEL_RANGO) - 1)
    return (float(r[k]), oof) if con_predicciones else float(r[k])


def dominio(df: pd.DataFrame, X: pd.DataFrame) -> dict:
    num = {c: {"min": float(X[c].min()), "max": float(X[c].max()), "mediana": float(X[c].median())}
           for c in D.NUMERICAS if X[c].notna().any()}
    return {"numericas": num,
            "localidades": {k: int(v) for k, v in df["localidad"].dropna().value_counts().items()},
            "estratos": {str(int(k)): int(v) for k, v in df["estrato"].dropna().value_counts().sort_index().items()}}


def referencia(X: pd.DataFrame) -> dict:
    """Apartamento típico del dataset: contra él se explica cada estimación."""
    ref = {c: float(X[c].median()) for c in D.NUMERICAS if X[c].notna().any()}
    reales = X.loc[X["localidad"] != "Sin dato", "localidad"]
    ref["localidad"] = reales.mode().iat[0] if len(reales) else "Sin dato"
    for c in ("habitaciones", "banos", "estrato", "parqueaderos"):
        if c in ref:
            ref[c] = float(round(ref[c]))
    return ref


def entrenar(ruta: pathlib.Path, tipo: str) -> dict:
    t0 = time.perf_counter()
    df = D.cargar(ruta)
    n = len(df)
    log.info("Entrenamiento con %s (%s): %d avisos", ruta.name, tipo, n)
    if n < MINIMO_AVISOS:
        log.warning("%s tiene %d avisos válidos; se necesitan al menos %d", ruta.name, n, MINIMO_AVISOS)
        raise SystemExit(f"{ruta.name} tiene {n} avisos válidos; se necesitan al menos {MINIMO_AVISOS}.")
    X, y = preparar(df), df[D.OBJETIVO].astype(float).to_numpy()
    Xe, Xp, ye, yp = train_test_split(X, y, test_size=0.2, random_state=SEMILLA)
    log.info("División: %d para entrenar, %d para la prueba final", len(ye), len(yp))
    log.debug("Vacíos por variable: %s", X.isna().sum().to_dict())

    cv, modelos = [], candidatos(len(ye))
    for nombre, m in modelos.items():
        t = time.perf_counter()
        pred = cross_val_predict(clone(m), Xe, ye, cv=pliegues(len(ye)))
        cv.append({"modelo": nombre, **medir(ye, pred)})
        log.info("Validación cruzada %-22s %s (%.1fs)", nombre, cv[-1], time.perf_counter() - t)
    mejor = min(cv, key=lambda r: r["mae"])
    log.info("Modelo elegido: %s (menor error absoluto medio)", mejor["modelo"])
    base = next(r for r in cv if r["modelo"] == "Línea base")

    elegido = modelos[mejor["modelo"]]
    prueba = {}
    for nombre in {mejor["modelo"], "Línea base"}:
        prueba[nombre] = medir(yp, clone(modelos[nombre]).fit(Xe, ye).predict(Xp))
    q_e = cuantil_conformal(elegido, Xe, ye)
    pred_p = clone(elegido).fit(Xe, ye).predict(Xp)
    dentro = np.abs(np.log(yp) - np.log(np.clip(pred_p, 1, None))) <= q_e
    prueba[mejor["modelo"]]["cobertura_rango"] = round(float(dentro.mean()), 3)
    log.info("Prueba final: %s", prueba)

    final = clone(elegido).fit(X, y)
    q, esperados = cuantil_conformal(elegido, X, y, con_predicciones=True)

    metricas = {
        "fecha": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "dataset": {"archivo": ruta.name, "tipo": tipo, "sha256": D.huella(ruta), "avisos": n,
                    "entrenamiento": len(ye), "prueba": len(yp),
                    "precio_mediano": float(np.median(y))},
        "modelo_elegido": mejor["modelo"],
        "supera_linea_base": mejor["modelo"] != "Línea base" and mejor["mae"] < base["mae"],
        "validacion_cruzada": {"pliegues": pliegues(len(ye)).n_splits, "resultados": cv},
        "prueba_final": prueba,
        "rango": {"nivel": NIVEL_RANGO, "ancho_log": round(q, 4),
                  "factor": round(math.exp(q), 3)},
        "variables": D.VARIABLES,
        "dominio": dominio(df, X),
    }
    ARTEFACTOS.mkdir(exist_ok=True)
    joblib.dump({"modelo": final, "nombre": mejor["modelo"], "q_log": q, "referencia": referencia(X),
                 "esperados": [round(float(v)) for v in esperados],
                 "dominio": metricas["dominio"], "sha256": metricas["dataset"]["sha256"]}, MODELO)
    METRICAS.write_text(json.dumps(metricas, ensure_ascii=False, indent=2), encoding="utf-8")
    registrar_mlflow(metricas)
    log.info("Modelo guardado en %s; métricas en %s. Entrenamiento total %.1fs (rango ±%.0f%%)",
             MODELO, METRICAS, time.perf_counter() - t0, (math.exp(q) - 1) * 100)
    if not metricas["supera_linea_base"]:
        log.warning("El modelo elegido NO supera a la línea base: los datos todavía no alcanzan para aprender bien")
    return metricas


def registrar_mlflow(m: dict) -> None:
    try:
        import mlflow
    except ImportError:
        return
    mlflow.set_tracking_uri((D.RAIZ / "mlruns").as_uri())
    mlflow.set_experiment("precio-apartamentos-bogota")
    with mlflow.start_run(run_name=m["modelo_elegido"]):
        mlflow.log_params({"dataset": m["dataset"]["archivo"], "avisos": m["dataset"]["avisos"],
                           "modelo": m["modelo_elegido"]})
        for r in m["validacion_cruzada"]["resultados"]:
            mlflow.log_metric(f"cv_mae_{r['modelo']}", r["mae"])
        mlflow.log_artifact(str(METRICAS))


def resumen(m: dict) -> str:
    d = m["dataset"]
    lineas = [f"Dataset: {d['archivo']} ({d['tipo']}), {d['avisos']} avisos "
              f"({d['entrenamiento']} entrenamiento / {d['prueba']} prueba)", "",
              f"Validación cruzada ({m['validacion_cruzada']['pliegues']} partes):",
              f"  {'Modelo':20s} {'MAE (M)':>9s} {'Error rel.':>10s} {'R²':>6s}"]
    for r in m["validacion_cruzada"]["resultados"]:
        lineas.append(f"  {r['modelo']:20s} {r['mae']/1e6:9.1f} {r['error_relativo']*100:9.1f}% "
                      f"{r['r2'] if r['r2'] is not None else '—':>6}")
    p = m["prueba_final"][m["modelo_elegido"]]
    lineas += ["", f"Elegido: {m['modelo_elegido']}"
               + ("" if m["supera_linea_base"] else "  (ningún modelo superó a la línea base)"),
               f"Prueba final: MAE {p['mae']/1e6:.1f} M, error relativo {p['error_relativo']*100:.1f} %, "
               f"cobertura del rango {p.get('cobertura_rango', 0)*100:.0f} % (meta {m['rango']['nivel']*100:.0f} %)"]
    if d["tipo"] == "demo":
        lineas += ["", "ATENCIÓN: son datos SINTÉTICOS de demostración. Estas métricas no valen para el proyecto."]
    elif d["avisos"] < 300:
        lineas += ["", f"Con {d['avisos']} avisos estas métricas son muy inestables; úsense solo como prueba del flujo."]
    return "\n".join(lineas)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", help="CSV a usar (por defecto, el corte más reciente en datos/)")
    args = ap.parse_args(argv)
    from comun import registro
    registro.configurar("entrenar")
    if args.dataset:
        ruta = pathlib.Path(args.dataset)
        tipo = "demo" if ruta.resolve() == D.DEMO.resolve() else "real"
    else:
        ruta, tipo = D.ubicar()
        if ruta is None:
            raise SystemExit("No hay dataset. Construya uno (README, 'Recolectar datos reales') "
                             "o cree datos de demostración con: python -m modelo.demo")
    print(resumen(entrenar(ruta, tipo)))


if __name__ == "__main__":
    main()
