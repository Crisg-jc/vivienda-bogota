"""Conversión de los textos extraídos a valores numéricos y categorías normalizadas."""
import csv
import datetime as dt
import pathlib
import re
import unicodedata

RAIZ = pathlib.Path(__file__).resolve().parents[1]

# 19 localidades urbanas de Bogotá D.C. (se excluye Sumapaz, rural)
LOCALIDADES = [
    "Usaquén", "Chapinero", "Santa Fe", "San Cristóbal", "Usme", "Tunjuelito", "Bosa",
    "Kennedy", "Fontibón", "Engativá", "Suba", "Barrios Unidos", "Teusaquillo",
    "Los Mártires", "Antonio Nariño", "Puente Aranda", "La Candelaria",
    "Rafael Uribe Uribe", "Ciudad Bolívar",
]


def simple(s: str | None) -> str:
    # pandas convierte un campo vacío en NaN (un float): sin esto se volvía el texto "nan" ("Nan" como barrio)
    if s is None or (isinstance(s, float) and s != s) or str(s).strip().lower() in ("nan", "none", "<na>", "null"):
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


_LOC_SIMPLE = {simple(l): l for l in LOCALIDADES}
_LOC_SIMPLE.update({"martires": "Los Mártires", "candelaria": "La Candelaria",
                    "rafael uribe": "Rafael Uribe Uribe", "santafe": "Santa Fe"})


def _cargar_barrios() -> dict[str, str]:
    """Tabla barrio -> localidad. Archivo opcional: datos/barrios_localidad.csv (columnas barrio,localidad).

    Se construye a partir de la capa de barrios de Datos Abiertos Bogotá / IDECA y se completa a
    mano con los barrios que reporte el script como 'sin localidad'.
    """
    ruta = RAIZ / "datos" / "barrios_localidad.csv"
    if not ruta.exists():
        return {}
    with ruta.open(encoding="utf-8") as f:
        return {simple(r["barrio"]): localidad(r["localidad"]) for r in csv.DictReader(f)
                if r.get("barrio") and localidad(r.get("localidad"))}


_BARRIOS = None


def localidad(texto: str | None, barrio: str | None = None) -> str | None:
    global _BARRIOS
    t = simple(texto)
    if t in _LOC_SIMPLE:
        return _LOC_SIMPLE[t]
    for clave, nombre in sorted(_LOC_SIMPLE.items(), key=lambda kv: -len(kv[0])):
        if t and re.search(rf"\b{re.escape(clave)}\b", t):
            return nombre
    if barrio:
        if _BARRIOS is None:
            _BARRIOS = _cargar_barrios()
        return _BARRIOS.get(simple(barrio))
    return None


def precio(texto: str | None) -> int | None:
    if not texto:
        return None
    t = str(texto).strip().lower()
    if re.fullmatch(r"\d+(\.\d{1,2})?", t):          # número limpio de un JSON: 350000000.0
        return int(float(t))
    millones = re.search(r"(\d+(?:[.,]\d+)?)\s*(millones|mill|m\b)", t)
    if millones and len(re.sub(r"\D", "", t)) <= 6:
        n = millones.group(1)
        n = n.replace(".", "") if re.fullmatch(r"\d{1,3}(\.\d{3})+", n) else n.replace(",", ".")
        return int(float(n) * 1_000_000)
    digitos = re.sub(r"\D", "", t.split(",")[0] if re.search(r",\d{2}$", t) else t)
    return int(digitos) if digitos else None


def area(texto: str | None) -> float | None:
    if not texto:
        return None
    t = str(texto).lower().replace("m²", "").replace("m2", "")
    m = re.search(r"\d+(?:[.,]\d+)*", t)
    if not m:
        return None
    n = m.group(0)
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", n):        # 1.250 -> miles
        n = n.replace(".", "")
    return float(n.replace(",", "."))


def entero(texto: str | None) -> int | None:
    if texto is None:
        return None
    m = re.search(r"\d+", str(texto))
    return int(m.group(0)) if m else None


def antiguedad(texto: str | None, hoy: dt.date | None = None) -> int | None:
    if not texto:
        return None
    t = simple(texto)
    hoy = hoy or dt.date.today()
    if any(p in t for p in ("estrenar", "nuevo", "menos de 1", "en construccion", "sobre planos")):
        return 0
    nums = [int(n) for n in re.findall(r"\d+", t)]
    if not nums:
        return None
    if len(nums) == 1 and 1900 < nums[0] <= hoy.year:   # año de construcción
        return hoy.year - nums[0]
    if len(nums) >= 2:                                  # "entre 10 y 20 años" -> 15
        return round((nums[0] + nums[1]) / 2)
    return nums[0]                                      # "más de 20 años" -> 20 (cota inferior)


TIPOS_EXCLUIDOS = ("casa", "lote", "oficina", "local", "bodega", "finca", "consultorio", "habitacion",
                   "proyecto")


def coordenada(texto: str | None, minimo: float, maximo: float) -> float | None:
    """Convierte una coordenada y la descarta si cae fuera de Bogotá urbana."""
    try:
        v = float(str(texto).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return round(v, 6) if minimo <= v <= maximo else None


def fecha(texto: str | None) -> dt.date | None:
    """Fecha de publicación del portal: ISO ('2026-10-04T13:20:00Z'), dd/mm/aaaa o época en ms/s."""
    if texto in (None, ""):
        return None
    t = str(texto).strip()
    if re.fullmatch(r"\d{10}(\d{3})?", t):
        n = int(t) / (1000 if len(t) == 13 else 1)
        d = dt.datetime.fromtimestamp(n, dt.timezone.utc).date()
    else:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", t) or None
        if m:
            d = dt.date(int(m[1]), int(m[2]), int(m[3]))
        else:
            m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", t)
            if not m:
                return None
            d = dt.date(int(m[3]), int(m[2]), int(m[1]))
    # Fuera de un rango razonable es un dato mal leído: mejor vacío que falso.
    return d if dt.date(2000, 1, 1) <= d <= dt.date.today() + dt.timedelta(days=1) else None


def estado(texto: str | None) -> str | None:
    t = simple(texto)
    if not t:
        return None
    if "usad" in t:
        return "usado"
    if any(p in t for p in ("nuev", "estrenar", "construccion", "planos")):
        return "nuevo"
    return None


def es_apartamento(tipo: str | None) -> bool:
    t = simple(tipo)
    return not t or not any(re.search(rf"\b{p}\b", t) for p in TIPOS_EXCLUIDOS)
