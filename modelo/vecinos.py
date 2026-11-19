"""Precio por m² de los vecinos más cercanos, como variable del modelo.

La localidad es una zona enorme (Suba tiene barrios de estrato 2 y de estrato 6). Lo que mejor
resume la ubicación es cuánto se pide por m² alrededor: este transformador busca los k avisos más
cercanos por coordenadas y entrega la mediana de su log(m²) y a qué distancia están.

Sin trampa: al entrenar, cada aviso se calcula SIN contarse a sí mismo, y dentro de la validación
cruzada solo con avisos de los otros pliegues (sklearn vuelve a ajustar el transformador en cada
pliegue). Avisos sin coordenadas quedan vacíos y el paso siguiente los imputa con la mediana.
"""
from __future__ import annotations

import math

import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.neighbors import NearestNeighbors

KM_LAT = 111.0
KM_LON = 111.0 * math.cos(math.radians(4.65))     # Bogotá


class VecinosM2(BaseEstimator, TransformerMixin):
    def __init__(self, k: int = 10):
        self.k = k

    @staticmethod
    def _puntos(X):
        a = np.asarray(X, dtype=float)
        return np.c_[a[:, 0] * KM_LAT, a[:, 1] * KM_LON], a[:, 2]

    def fit(self, X, y=None):
        P, area = self._puntos(X)
        ok = ~np.isnan(P).any(axis=1) & ~np.isnan(area) & (area > 0)
        if y is None:
            ok[:] = False
        self.ok_ = ok
        self.P_ = P[ok]
        # y llega en escala logarítmica (el modelo aprende log(precio)): log(m²) = y − log(área)
        self.v_ = (np.asarray(y, dtype=float)[ok] - np.log(area[ok])) if y is not None else np.array([])
        self.arbol_ = NearestNeighbors(n_neighbors=min(self.k + 1, len(self.P_))).fit(self.P_) if len(self.P_) >= 3 else None
        return self

    def _calcular(self, X, excluir_propios: bool):
        P, _ = self._puntos(X)
        salida = np.full((len(P), 2), np.nan)
        if self.arbol_ is None:
            return salida
        filas = np.where(~np.isnan(P).any(axis=1))[0]
        if not len(filas):
            return salida
        dist, idx = self.arbol_.kneighbors(P[filas])
        propio = np.full(len(P), -1)
        if excluir_propios:          # índice de cada fila de entrenamiento dentro de P_
            propio[np.where(self.ok_)[0]] = np.arange(self.ok_.sum())
        for j, fila in enumerate(filas):
            usar = idx[j] != propio[fila] if excluir_propios else np.ones(len(idx[j]), bool)
            vecinos, d = idx[j][usar][: self.k], dist[j][usar][: self.k]
            if len(vecinos):
                salida[fila] = (np.median(self.v_[vecinos]), np.mean(d))
        return salida

    def transform(self, X):
        return self._calcular(X, excluir_propios=False)

    def fit_transform(self, X, y=None, **_):
        return self.fit(X, y)._calcular(X, excluir_propios=True)

    def get_feature_names_out(self, input_features=None):
        return np.array(["m2_vecinos_log", "distancia_vecinos_km"])
