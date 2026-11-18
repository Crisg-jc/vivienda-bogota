"""Extracción de campos de la página de detalle de un aviso.

Se usan tres métodos, del más confiable al menos confiable:
  1. JSON-LD (datos estructurados schema.org que muchos portales publican para buscadores)
  2. __NEXT_DATA__ u otro JSON embebido (sitios hechos con Next.js, como Finca Raíz)
  3. Texto visible con expresiones regulares (último recurso)
Para cada campo se toma el primer método que lo encuentre.

Solo se extraen los campos de la lista blanca CAMPOS. Nombres, teléfonos, correos o
datos del anunciante nunca se leen ni se guardan (Ley 1581 de 2012, minimización).
"""
import json
import logging
import re
import unicodedata

from bs4 import BeautifulSoup

log = logging.getLogger("vivienda.extraer")

CAMPOS = ["precio", "area", "habitaciones", "banos", "estrato", "parqueaderos",
          "antiguedad", "administracion", "localidad", "barrio", "tipo",
          "piso", "estado", "latitud", "longitud"]

SINONIMOS = {
    "precio": {"price", "precio", "saleprice", "sellprice", "pricevalue", "valorventa"},
    "area": {"area", "m2", "builtarea", "areaconstruida", "areaprivada", "privatearea",
             "livingarea", "floorsize", "m2built", "areatotal"},
    # 'rooms' NO se usa: en Finca Raíz significa "ambientes", no habitaciones.
    "habitaciones": {"bedrooms", "habitaciones", "alcobas", "numberofbedrooms"},
    "banos": {"baths", "bathrooms", "banos", "numberofbathroomstotal", "numberofbathrooms"},
    "estrato": {"stratum", "estrato", "socioeconomiclevel"},
    "parqueaderos": {"garages", "garage", "parking", "parqueaderos", "parkinglots",
                     "numberofparkingspaces"},
    "antiguedad": {"antiguedad", "age", "yearbuilt", "constructionyear", "propertyage"},
    "administracion": {"administracion", "adminprice", "commonexpenses", "admonvalue",
                       "administrationfee", "valoradministracion"},
    "localidad": {"localidad"},
    "barrio": {"barrio", "neighbourhood", "neighborhood", "sector", "commonneighborhood"},
    "tipo": {"propertytype", "tipoinmueble", "tipo"},
}


def _clave(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


_INDICE = {_clave(sin): campo for campo, sins in SINONIMOS.items() for sin in sins}


def _escalar(v):
    """Reduce {'name': 'Suba'} o {'value': 75} a un texto; descarta listas y objetos grandes."""
    if isinstance(v, dict):
        for k in ("value", "name", "amount", "nombre", "valor"):
            if k in v and not isinstance(v[k], (dict, list)):
                return str(v[k])
        return None
    if isinstance(v, (list, tuple)) or v is None or v == "":
        return None
    return str(v)


def _mejor_objeto(datos) -> dict:
    """Recorre un JSON y devuelve los campos del objeto que más se parece a un aviso.

    Una página trae también avisos 'similares' o recomendados; por eso no se toma la primera
    clave 'price' que aparezca, sino el objeto que contiene más campos del aviso a la vez.
    """
    mejor, mejor_puntaje = {}, 0
    pila = [datos]
    while pila:
        nodo = pila.pop()
        if isinstance(nodo, dict):
            encontrados = {}
            for k, v in nodo.items():
                campo = _INDICE.get(_clave(k))
                if campo and campo not in encontrados:
                    valor = _escalar(v)
                    if valor is not None:
                        encontrados[campo] = valor
                if isinstance(v, (dict, list)):
                    pila.append(v)
            # Algunos portales anidan sub-objetos (p. ej. 'location': {'barrio': ...})
            for k in ("location", "address", "ubicacion", "locations"):
                sub = nodo.get(k)
                if isinstance(sub, dict):
                    for kk, vv in sub.items():
                        campo = _INDICE.get(_clave(kk))
                        if campo in ("localidad", "barrio") and campo not in encontrados:
                            valor = _escalar(vv)
                            if valor:
                                encontrados[campo] = valor
            puntaje = len(encontrados) + (3 if {"precio", "area"} <= encontrados.keys() else 0)
            if puntaje > mejor_puntaje:
                mejor, mejor_puntaje = encontrados, puntaje
        elif isinstance(nodo, list):
            pila.extend(nodo)
    return mejor if {"precio", "area"} <= mejor.keys() or len(mejor) >= 3 else {}


TIPOS_LD_VIVIENDA = {"Apartment", "House", "SingleFamilyResidence", "Residence", "Accommodation"}


def _json_ld(soup) -> dict:
    res = {}
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            datos = json.loads(s.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        objetos = datos if isinstance(datos, list) else datos.get("@graph", [datos])
        for o in objetos:
            if not isinstance(o, dict):
                continue
            ofertas = o.get("offers") or {}
            if isinstance(ofertas, list):
                ofertas = ofertas[0] if ofertas else {}
            if isinstance(ofertas, dict) and ofertas.get("price"):
                res.setdefault("precio", str(ofertas["price"]))
            if o.get("floorSize"):
                res.setdefault("area", _escalar(o["floorSize"]) or str(o["floorSize"]))
            for k_ld, campo in (("numberOfBedrooms", "habitaciones"), ("numberOfRooms", "habitaciones"),
                                ("numberOfBathroomsTotal", "banos"), ("yearBuilt", "antiguedad")):
                if o.get(k_ld) is not None:
                    res.setdefault(campo, _escalar(o[k_ld]) or str(o[k_ld]))
            tipos = o.get("@type") or []
            tipos = [tipos] if isinstance(tipos, str) else tipos
            vivienda = [t for t in tipos if t in TIPOS_LD_VIVIENDA]
            if vivienda:
                res.setdefault("tipo", vivienda[0])
    return {k: v for k, v in res.items() if v}


def _primero_nombre(lista):
    if isinstance(lista, list) and lista and isinstance(lista[0], dict):
        return lista[0].get("name")
    return None


def _fincaraiz(datos: dict) -> dict:
    """Lee el objeto del aviso de Finca Raíz (props.pageProps.data) por sus claves exactas.

    Estructura verificada con un aviso real el 2026-10-04. De este objeto se IGNORAN a
    propósito 'owner', 'seller', 'address' y cualquier dato de contacto.
    """
    d = (datos.get("props") or {}).get("pageProps", {}).get("data")
    if not isinstance(d, dict) or "bedrooms" not in d or "price" not in d:
        return {}
    ficha = {f.get("field"): f.get("value") for f in d.get("technicalSheet") or []
             if isinstance(f, dict)}
    loc = d.get("locations") or {}
    precio = d["price"].get("amount") if isinstance(d["price"], dict) else d["price"]
    gastos = d.get("commonExpenses") or {}
    tipo = (d.get("property_type") or {}).get("name")
    if d.get("isProject") or d.get("isProjectUnit"):
        tipo = f"proyecto {tipo or ''}".strip()
    campos = {
        "precio": precio,
        "area": d.get("m2Built") or d.get("m2"),
        "habitaciones": d.get("bedrooms"),
        "banos": d.get("bathrooms"),
        "estrato": d.get("stratum"),
        "parqueaderos": d.get("garage"),
        "antiguedad": ficha.get("constructionYear") or d.get("construction_year"),
        "administracion": gastos.get("amount") if isinstance(gastos, dict) else None,
        "localidad": _primero_nombre(loc.get("locality")),
        "barrio": _primero_nombre(loc.get("neighbourhood"))
                  or (loc.get("location_main") or {}).get("name"),
        "tipo": tipo,
        "piso": d.get("floor") or None,
        "estado": ficha.get("construction_state_name"),
        "latitud": d.get("latitude"),
        "longitud": d.get("longitude"),
    }
    return {k: str(v) for k, v in campos.items() if v not in (None, "", [], {})}


def _json_embebido(soup) -> tuple[str, dict]:
    s = soup.find("script", id="__NEXT_DATA__")
    if not s or not s.string:
        return "next-data", {}
    try:
        datos = json.loads(s.string)
    except json.JSONDecodeError:
        return "next-data", {}
    especifico = _fincaraiz(datos)
    if especifico:
        return "fincaraiz-json", especifico
    return "next-data", _mejor_objeto(datos)


_RE_TEXTO = {
    "precio": r"\$\s*([\d]{1,3}(?:[.,]\d{3}){2,})",
    "area": r"(\d{2,4}(?:[.,]\d{1,2})?)\s*m(?:²|2|ts2)\b",
    "habitaciones": r"(\d{1,2})\s*(?:habitaci|alcoba)",
    "banos": r"(\d{1,2})\s*baño",
    "estrato": r"estrato\s*:?\s*(\d)",
    "parqueaderos": r"(\d{1,2})\s*(?:parqueadero|garaje)",
    "antiguedad": r"antig[uü]edad\s*:?\s*([^\n|]{1,30})",
    "administracion": r"administraci[oó]n\s*:?\s*\$?\s*([\d.,]{5,})",
}


def _texto(soup) -> dict:
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    texto = soup.get_text("\n", strip=True)
    res = {}
    for campo, patron in _RE_TEXTO.items():
        m = re.search(patron, texto, re.I)
        if m:
            res[campo] = m.group(1).strip()
    return res


def extraer(html: str, url: str, patron_barrio_url: str | None = None) -> tuple[dict, str]:
    """Devuelve (campos_en_texto, metodo_principal)."""
    soup = BeautifulSoup(html, "lxml")
    # El JSON propio del portal va primero: es el más completo y preciso.
    capas = [_json_embebido(soup), ("json-ld", _json_ld(soup))]
    capas.append(("texto", _texto(soup)))   # al final: destruye scripts del árbol
    campos, metodos = {}, []
    for nombre, datos in capas:
        nuevos = {k: v for k, v in datos.items() if k in CAMPOS and k not in campos}
        if nuevos:
            metodos.append(nombre)
            campos.update(nuevos)
    if "barrio" not in campos and patron_barrio_url:
        m = re.search(patron_barrio_url, url)
        if m:
            campos["barrio"] = m.group(1).replace("-", " ")
            log.debug("Barrio tomado de la URL: %s", campos["barrio"])
    log.debug("Extracción de %s: métodos=%s campos=%s", url, metodos or "ninguno", campos)
    if not metodos:
        log.warning("No se encontró ningún dato en %s (%d caracteres de HTML)", url, len(html))
    return campos, "+".join(metodos) or "ninguno"


def enlaces_detalle(html: str, base: str, patron: str) -> list[str]:
    """Encuentra en una página de listado las URLs de avisos individuales.

    Busca en dos lugares: los enlaces <a href> y el texto completo de la página (incluidos
    los datos JSON internos), porque algunos portales arman las tarjetas con JavaScript y
    las rutas de los avisos solo aparecen en sus datos, no como enlaces HTML.
    """
    from urllib.parse import urljoin
    rx = re.compile(patron)
    candidatos = [a["href"] for a in BeautifulSoup(html, "lxml").find_all("a", href=True)]
    texto = html.replace("\\/", "/").replace("\\u002F", "/")
    candidatos += re.findall(r'(?:https?://[a-z0-9.-]+)?/[a-z0-9][a-z0-9/_-]{8,}', texto, re.I)
    vistos, salida = set(), []
    for c in candidatos:
        url = urljoin(base, c).split("?")[0].split("#")[0].rstrip("/")
        if rx.match(url) and url not in vistos:
            vistos.add(url)
            salida.append(url)
    log.debug("Listado: %d candidatos revisados, %d avisos individuales", len(candidatos), len(salida))
    return salida
