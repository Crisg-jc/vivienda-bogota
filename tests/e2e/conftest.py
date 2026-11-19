"""Servidor real para las pruebas de regresión en el navegador.

Arranca la app en un proceso aparte, con datos sintéticos fijos (semilla 11) y TODO en carpetas
temporales: base, modelo, estado, registros. Nunca toca datos/ ni artefactos/ del proyecto.
Se omiten si Playwright o su Chromium no están instalados (en CI hay un trabajo que los instala).
"""
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

pw = pytest.importorskip("playwright.sync_api")


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def servidor(tmp_path_factory):
    raiz = tmp_path_factory.mktemp("e2e")
    from modelo import demo
    ruta = raiz / "dataset_v1.csv"
    demo.generar(900, 11).to_csv(ruta, index=False)
    puerto = _puerto_libre()
    env = {**os.environ, "VIVIENDA_DATASET": str(ruta), "VIVIENDA_ARTEFACTOS": str(raiz / "artefactos"),
           "DATABASE_URL": f"sqlite:///{raiz}/e2e.db", "VIVIENDA_ESTADO": str(raiz / "estado"),
           "VIVIENDA_REGISTROS": str(raiz / "registros"), "VIVIENDA_AUTOMATICO": "0"}
    raiz_proyecto = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    p = subprocess.Popen([sys.executable, "-m", "app", "--port", str(puerto)], cwd=raiz_proyecto, env=env,
                         stdout=open(raiz / "servidor.log", "w"), stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{puerto}"
    for _ in range(240):
        try:
            with urllib.request.urlopen(url + "/api/salud", timeout=2) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.5)
    else:
        p.terminate()
        raise RuntimeError("La app no arrancó; vea " + str(raiz / "servidor.log"))
    yield url
    p.terminate()
    p.wait(10)


@pytest.fixture(scope="session")
def navegador():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"Chromium de Playwright no disponible: {e}")
        yield b
        b.close()


@pytest.fixture()
def pagina(navegador, servidor):
    """Página nueva que falla la prueba si hay errores de JavaScript o en la consola."""
    # bypass_csp: solo para que la prueba pueda evaluar expresiones e inyectar axe; la app no cambia
    ctx = navegador.new_context(viewport={"width": 1528, "height": 740}, bypass_csp=True)
    # Las calles del mapa vienen de internet: en la prueba se bloquean (no son parte de la app)
    ctx.route("**/basemaps.cartocdn.com/**", lambda r: r.abort())
    pg = ctx.new_page()
    errores = []
    pg.on("pageerror", lambda e: errores.append(f"JS: {e}"))
    pg.on("console", lambda m: m.type == "error" and "cartocdn" not in m.text and "Failed to load resource" not in m.text
          and errores.append(f"consola: {m.text}"))
    pg.base = servidor
    yield pg
    ctx.close()
    assert errores == [], errores
