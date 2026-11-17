"""Genera un dataset SINTÉTICO para probar la aplicación sin recolectar datos.

Uso:
  python -m modelo.demo            # crea datos/demo/dataset_demo.csv con 600 filas

Estos datos son inventados por un programa. Siguen relaciones razonables (el precio crece con el
área y el estrato) solo para que la interfaz tenga algo que mostrar. NO describen el mercado de
Bogotá y no deben usarse para conclusiones, informes ni métricas del proyecto. La API y la
interfaz los marcan como demostración.
"""
import argparse

import numpy as np
import pandas as pd

from limpieza.normalizar import LOCALIDADES
from .datos import DEMO

# Estrato típico y centro aproximado de cada localidad (solo para dispersar puntos en el mapa).
ZONAS = {
    "Usaquén": (5, 4.715, -74.035), "Chapinero": (5, 4.650, -74.060), "Santa Fe": (3, 4.610, -74.070),
    "San Cristóbal": (2, 4.565, -74.085), "Usme": (2, 4.500, -74.110), "Tunjuelito": (3, 4.575, -74.135),
    "Bosa": (2, 4.615, -74.190), "Kennedy": (3, 4.630, -74.150), "Fontibón": (4, 4.675, -74.140),
    "Engativá": (3, 4.705, -74.110), "Suba": (4, 4.745, -74.080), "Barrios Unidos": (4, 4.670, -74.075),
    "Teusaquillo": (4, 4.640, -74.080), "Los Mártires": (3, 4.605, -74.090),
    "Antonio Nariño": (3, 4.590, -74.100), "Puente Aranda": (3, 4.615, -74.115),
    "La Candelaria": (3, 4.597, -74.072), "Rafael Uribe Uribe": (2, 4.570, -74.115),
    "Ciudad Bolívar": (2, 4.540, -74.150),
}
M2_ESTRATO = {1: 2.6e6, 2: 3.4e6, 3: 4.6e6, 4: 6.6e6, 5: 8.6e6, 6: 10.8e6}  # supuestos de demostración


def generar(n: int, semilla: int) -> pd.DataFrame:
    rng = np.random.default_rng(semilla)
    pesos = np.array([3 if ZONAS[l][0] >= 4 else 2 for l in LOCALIDADES], float)
    locs = rng.choice(LOCALIDADES, size=n, p=pesos / pesos.sum())
    filas = []
    for loc in locs:
        base, lat, lon = ZONAS[loc]
        estrato = int(np.clip(base + rng.choice([-1, 0, 0, 0, 1]), 1, 6))
        area = float(np.clip(rng.lognormal(np.log(45 + 12 * estrato), 0.32), 28, 400))
        hab = int(np.clip(round(area / 28 + rng.normal(0, 0.6)), 1, 5))
        banos = int(np.clip(round(hab * 0.6 + estrato * 0.15 + rng.normal(0, 0.4)), 1, 5))
        parq = int(np.clip(round((estrato - 2) * 0.55 + rng.normal(0, 0.5)), 0, 4))
        antig = float(np.clip(rng.gamma(2.0, 7.0), 0, 60))
        m2 = M2_ESTRATO[estrato] * (1 - 0.004 * antig) * (1 + 0.04 * parq) * rng.lognormal(0, 0.14)
        precio = round(m2 * area, -6)
        if not 80e6 <= precio <= 3e9:
            continue
        filas.append({
            "precio": int(precio), "area_m2": round(area, 1), "habitaciones": hab, "banos": banos,
            "estrato": estrato, "parqueaderos": parq, "localidad": loc, "barrio": None,
            "antiguedad": round(antig), "administracion": int(round(area * (2500 + 900 * estrato), -3)),
            "piso": int(rng.integers(1, 16)), "estado": "usado" if antig > 1 else "nuevo",
            "latitud": round(lat + rng.normal(0, 0.012), 6), "longitud": round(lon + rng.normal(0, 0.010), 6),
            "portal": "demo-sintetico", "fecha_consulta": None, "url_origen": None,
        })
    return pd.DataFrame(filas)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--filas", type=int, default=600)
    ap.add_argument("--semilla", type=int, default=7)
    args = ap.parse_args(argv)
    df = generar(args.filas, args.semilla)
    DEMO.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(DEMO, index=False, encoding="utf-8")
    print(f"Dataset SINTÉTICO de demostración: {len(df)} filas en {DEMO.relative_to(DEMO.parents[2])}")


if __name__ == "__main__":
    main()
