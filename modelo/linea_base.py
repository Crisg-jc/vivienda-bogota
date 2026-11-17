"""Línea base del proyecto. Vive en su propio módulo para que el modelo guardado con joblib
se pueda cargar desde cualquier punto de entrada (CLI o aplicación)."""
import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, RegressorMixin


class LineaBase(BaseEstimator, RegressorMixin):
    """Precio mediano del m² en la misma localidad y estrato, multiplicado por el área.

    Si esa combinación tiene menos de `minimo` avisos, usa el estrato; si tampoco, la ciudad.
    Es la cuenta que haría una persona con una hoja de cálculo: cualquier modelo debe superarla.
    """

    def __init__(self, minimo: int = 3):
        self.minimo = minimo

    def fit(self, X, y):
        m2 = pd.Series(np.asarray(y, float) / X["area_m2"].to_numpy(), index=X.index)
        d = X.assign(_m2=m2)
        g = d.groupby(["localidad", "estrato"])["_m2"].agg(["median", "size"])
        self.por_loc_estrato_ = g.loc[g["size"] >= self.minimo, "median"].to_dict()
        e = d.groupby("estrato")["_m2"].agg(["median", "size"])
        self.por_estrato_ = e.loc[e["size"] >= self.minimo, "median"].to_dict()
        self.ciudad_ = float(m2.median())
        return self

    def predict(self, X):
        salida = []
        for loc, est, area in zip(X["localidad"], X["estrato"], X["area_m2"]):
            m2 = self.por_loc_estrato_.get((loc, est), self.por_estrato_.get(est, self.ciudad_))
            salida.append(m2 * area)
        return np.asarray(salida, float)
