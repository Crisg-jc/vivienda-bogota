"""Cliente HTTP respetuoso: robots.txt, pausas, reintentos limitados y freno ante bloqueos."""
import random
import re
import time
import urllib.parse

import requests


class BloqueoDelPortal(Exception):
    """El portal respondió 403/429 o mostró un reto anti-bots: hay que detenerse, no insistir."""


class AvisoRetirado(Exception):
    """El aviso ya no está publicado: el portal respondió 404/410 o redirigió fuera del aviso."""


class Robots:
    """Intérprete de robots.txt con soporte de comodines (* y $).

    Se implementa a mano porque urllib.robotparser de Python NO entiende comodines,
    y Finca Raíz los usa (p. ej. 'Disallow: /venta/*-y-*').
    """

    def __init__(self, texto: str, agente: str):
        self.reglas: list[tuple[bool, str]] = []   # (permitido, patron)
        grupos, actual, en_reglas = [], None, False
        for linea in texto.splitlines():
            linea = linea.split("#", 1)[0].strip()
            if ":" not in linea:
                continue
            clave, valor = (s.strip() for s in linea.split(":", 1))
            clave = clave.lower()
            if clave == "user-agent":
                if actual is None or en_reglas:
                    actual = {"agentes": [], "reglas": []}
                    grupos.append(actual)
                    en_reglas = False
                actual["agentes"].append(valor.lower())
            elif clave in ("allow", "disallow") and actual is not None:
                en_reglas = True
                if valor:
                    actual["reglas"].append((clave == "allow", valor))
        agente = agente.lower()
        propios = [g for g in grupos if any(a != "*" and a in agente for a in g["agentes"])]
        elegidos = propios or [g for g in grupos if "*" in g["agentes"]]
        for g in elegidos:
            self.reglas.extend(g["reglas"])

    @staticmethod
    def _regex(patron: str) -> re.Pattern:
        fin = patron.endswith("$")
        cuerpo = re.escape(patron.rstrip("$")).replace(r"\*", ".*")
        return re.compile("^" + cuerpo + ("$" if fin else ""))

    def permitido(self, url: str) -> bool:
        p = urllib.parse.urlsplit(url)
        ruta = (p.path or "/") + (f"?{p.query}" if p.query else "")
        mejor = None  # la regla más específica (patrón más largo) gana; empate -> Allow
        for permitido, patron in self.reglas:
            if self._regex(patron).match(ruta):
                clave = (len(patron), permitido)
                if mejor is None or clave > mejor:
                    mejor = clave
        return True if mejor is None else mejor[1]


class Cliente:
    def __init__(self, conf_general: dict):
        self.conf = conf_general
        self.sesion = requests.Session()
        self.sesion.headers.update({
            "User-Agent": conf_general["user_agent"],
            "Accept-Language": "es-CO,es;q=0.9",
        })
        self._robots: dict[str, Robots] = {}
        self._ultima = 0.0

    def robots(self, base: str) -> Robots:
        if base not in self._robots:
            r = self.sesion.get(base.rstrip("/") + "/robots.txt", timeout=self.conf["timeout_s"])
            r.raise_for_status()
            self._robots[base] = Robots(r.text, self.conf["user_agent"])
        return self._robots[base]

    def _pausa(self):
        espera = random.uniform(self.conf["pausa_min_s"], self.conf["pausa_max_s"])
        transcurrido = time.monotonic() - self._ultima
        if transcurrido < espera:
            time.sleep(espera - transcurrido)
        self._ultima = time.monotonic()

    def get(self, url: str, base: str) -> str:
        return self.obtener(url, base)[0]

    def obtener(self, url: str, base: str) -> tuple[str, str]:
        """Devuelve (html, url_final). 404 y 410 se informan como AvisoRetirado."""
        if not self.robots(base).permitido(url):
            raise PermissionError(f"robots.txt no permite {url}")
        for intento in range(self.conf["reintentos"] + 1):
            self._pausa()
            try:
                r = self.sesion.get(url, timeout=self.conf["timeout_s"])
            except requests.RequestException:
                if intento == self.conf["reintentos"]:
                    raise
                continue
            if r.status_code in (403, 429):
                raise BloqueoDelPortal(f"{r.status_code} en {url}")
            if r.status_code in (404, 410):
                raise AvisoRetirado(f"{r.status_code} en {url}")
            if r.status_code >= 500 and intento < self.conf["reintentos"]:
                time.sleep(15 * (intento + 1))
                continue
            r.raise_for_status()
            if re.search(r"captcha|_Incapsula_Resource|cf-challenge", r.text[:5000], re.I):
                raise BloqueoDelPortal(f"reto anti-bots en {url}")
            return r.text, r.url
        raise RuntimeError(f"sin respuesta válida para {url}")
