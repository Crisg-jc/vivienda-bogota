"""Asigna la localidad a partir de las coordenadas del aviso (punto dentro de polígono).

Usa el mapa oficial de localidades de Datos Abiertos Bogotá (licencia CC BY 4.0):
  https://datosabiertos.bogota.gov.co/dataset/localidad-bogota-d-c
Se descarga una sola vez con:  python -m limpieza.geo descargar
y queda en datos/localidades.geojson. Implementado sin librerías geográficas adicionales.
"""
import json
import pathlib
import sys

from . import normalizar as nz

RAIZ = pathlib.Path(__file__).resolve().parents[1]
ARCHIVO = RAIZ / "datos" / "localidades.geojson"
URL = ("https://datosabiertos.bogota.gov.co/dataset/856cb657-8ca3-4ee8-857f-37211173b1f8/"
       "resource/497b8756-0927-4aee-8da9-ca4e32ca3a8a/download/loca.json")

_POLIGONOS = None   # lista de (nombre_localidad, [anillos], caja)


def _nombre(props: dict) -> str | None:
    """Toma el atributo cuyo valor sea el nombre de una localidad (no depende del nombre del campo)."""
    for v in props.values():
        if isinstance(v, str):
            loc = nz.localidad(v)
            if loc:
                return loc
    return None


def _anillos_de(g: dict) -> list:
    """Devuelve una lista de partes; cada parte es una lista de anillos [[x, y], ...].

    Acepta GeoJSON estándar (Polygon / MultiPolygon) y Esri JSON de ArcGIS ({'rings': [...]}).
    En Esri JSON todos los anillos (exteriores y huecos) van juntos en una sola parte; la regla
    par-impar de _dentro_parte los resuelve sin necesidad de distinguirlos.
    """
    if "rings" in g:
        return [g["rings"]]
    if g.get("type") == "Polygon":
        return [g["coordinates"]]
    if g.get("type") == "MultiPolygon":
        return list(g["coordinates"])
    return []


def _conversor(datos: dict):
    """Si el mapa viene en metros (sistema proyectado), devuelve una función que pasa a grados."""
    ref = datos.get("spatialReference") or {}
    epsg = ref.get("latestWkid") or ref.get("wkid")
    if not epsg:
        crs = ((datos.get("crs") or {}).get("properties") or {}).get("name", "")
        digitos = "".join(c for c in crs.split(":")[-1] if c.isdigit())
        epsg = int(digitos) if digitos else None
    if not epsg or int(epsg) in (4326, 4686, 4170):     # ya está en grados
        return None
    try:
        from pyproj import Transformer
    except ImportError:
        raise SystemExit(f"El mapa está en el sistema EPSG:{epsg} (metros). Instale el conversor con:\n"
                         "  python -m pip install pyproj\ny vuelva a ejecutar el comando.") from None
    t = Transformer.from_crs(int(epsg), 4326, always_xy=True)
    return lambda x, y: list(t.transform(x, y))


def _cargar():
    global _POLIGONOS
    if _POLIGONOS is not None:
        return _POLIGONOS
    _POLIGONOS = []
    if not ARCHIVO.exists():
        return _POLIGONOS
    datos = json.loads(ARCHIVO.read_text(encoding="utf-8-sig"))
    convertir = _conversor(datos)
    for f in datos.get("features", []):
        props = f.get("properties") or f.get("attributes") or {}
        nombre = _nombre(props)
        g = f.get("geometry") or {}
        if not nombre:
            continue
        for anillos in _anillos_de(g):
            if convertir:
                anillos = [[convertir(p[0], p[1]) for p in anillo] for anillo in anillos]
            puntos = [p for anillo in anillos for p in anillo]
            if not puntos:
                continue
            xs = [p[0] for p in puntos]
            ys = [p[1] for p in puntos]
            if max(abs(min(xs)), abs(max(xs))) > 180:
                raise ValueError(
                    "El mapa viene en coordenadas proyectadas (metros), no en grados. "
                    f"Ejemplo de punto: {puntos[0]}. Envíe este mensaje para ajustarlo.")
            _POLIGONOS.append((nombre, anillos, (min(xs), min(ys), max(xs), max(ys))))
    return _POLIGONOS


def estructura() -> str:
    """Resumen del archivo para diagnosticar formatos desconocidos."""
    datos = json.loads(ARCHIVO.read_text(encoding="utf-8-sig"))
    f = (datos.get("features") or [{}])[0]
    g = f.get("geometry") or {}
    props = f.get("properties") or f.get("attributes") or {}
    return (f"claves del archivo: {list(datos.keys())}\n"
            f"claves de cada elemento: {list(f.keys())}\n"
            f"atributos: {props}\n"
            f"claves de la geometría: {list(g.keys())}\n"
            f"sistema de referencia: {datos.get('spatialReference') or datos.get('crs')}\n"
            f"inicio de la geometría: {str(g)[:200]}")


def _dentro(x, y, anillo) -> bool:
    adentro, j = False, len(anillo) - 1
    for i in range(len(anillo)):
        xi, yi = anillo[i][0], anillo[i][1]
        xj, yj = anillo[j][0], anillo[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            adentro = not adentro
        j = i
    return adentro


def _dentro_parte(x, y, anillos) -> bool:
    # Regla par-impar sobre todos los anillos: los huecos quedan fuera automáticamente
    return sum(_dentro(x, y, a) for a in anillos) % 2 == 1


def localidad_por_coordenadas(lat: float | None, lon: float | None) -> str | None:
    if lat is None or lon is None:
        return None
    for nombre, anillos, (x0, y0, x1, y1) in _cargar():
        if x0 <= lon <= x1 and y0 <= lat <= y1 and _dentro_parte(lon, lat, anillos):
            return nombre
    return None


def descargar():
    import requests
    ARCHIVO.parent.mkdir(exist_ok=True)
    r = requests.get(URL, timeout=120)
    r.raise_for_status()
    ARCHIVO.write_bytes(r.content)
    global _POLIGONOS
    _POLIGONOS = None
    n = len({p[0] for p in _cargar()})
    print(f"Mapa guardado en {ARCHIVO} con {n} localidades reconocidas.")
    if n < 19:
        print("Atención: se esperaban al menos 19 localidades urbanas. Estructura del archivo:")
        print(estructura())


if __name__ == "__main__":
    if sys.argv[1:] == ["descargar"]:
        descargar()
    elif sys.argv[1:] == ["revisar"]:
        print(estructura())
        print("Localidades reconocidas:", sorted({p[0] for p in _cargar()}))
        print("Prueba (Parque de la 93, debe ser Chapinero):", localidad_por_coordenadas(4.6765, -74.0485))
    else:
        print(__doc__)
