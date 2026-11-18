"""Línea base del proyecto. Vive en su propio módulo para que el modelo guardado con joblib
se pueda cargar desde cualquier punto de entrada (CLI o aplicación)."""
import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, RegressorMixin


class LineaBase(BaseEstimator, RegressorMixin):
    """Precio mediano del m² en la misma localidad y estrato, multiplicado por el área.

    Si esa combinación tiene menos de `minimo` avisos, usa el estrato. Si el estrato tampoco
    tiene suficientes, lo deduce de la tendencia entre estratos (el m² sube de forma parecida de
    un estrato al siguiente), en vez de usar la mediana de toda la ciudad: con pocos datos, eso
    hacía que un estrato 1 sin avisos saliera más caro que un estrato 4.
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
        # log(m² mediano) = a + b·estrato, ponderado por cuántos avisos tiene cada estrato
        ok = e[e["size"] >= self.minimo]
        if len(ok) >= 2:
            b, a = np.polyfit(ok.index.astype(float), np.log(ok["median"].astype(float)), 1,
                              w=np.sqrt(ok["size"].astype(float)))
            self.tendencia_ = (float(a), float(max(b, 0.0)))   # nunca más barato al subir de estrato
        else:
            self.tendencia_ = None
        return self

    def _m2_estrato(self, est):
        if est in self.por_estrato_:
            return self.por_estrato_[est]
        tendencia = getattr(self, "tendencia_", None)       # modelos guardados antes no la tienen
        if tendencia is None or est is None or (isinstance(est, float) and np.isnan(est)):
            return self.ciudad_
        a, b = tendencia
        return float(np.exp(a + b * float(est)))

    def predict(self, X):
        salida = []
        for loc, est, area in zip(X["localidad"], X["estrato"], X["area_m2"], strict=True):
            m2 = self.por_loc_estrato_.get((loc, est))
            if m2 is None:
                m2 = self._m2_estrato(est)
            salida.append(m2 * area)
        return np.asarray(salida, float)
