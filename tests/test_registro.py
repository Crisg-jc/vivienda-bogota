"""El registro (logs): archivo, errores del navegador, diagnóstico y trazas de errores."""
import logging

from comun import registro
from tests.test_app import cliente  # noqa: F401  (misma app ya arrancada)


def _texto():
    for h in logging.getLogger().handlers:
        h.flush()
    return registro.GENERAL.read_text(encoding="utf-8")


def test_cada_solicitud_queda_registrada_con_su_rid(cliente):  # noqa: F811
    c, _ = cliente
    r = c.get("/api/estado", headers={"X-Request-ID": "prueba-rid-123"})
    assert r.headers["X-Request-ID"] == "prueba-rid-123"
    assert "GET /api/estado -> 200" in _texto()
    assert "rid=prueba-rid-123" in _texto()


def test_estimacion_y_datos_invalidos_se_registran(cliente):  # noqa: F811
    c, _ = cliente
    c.post("/api/estimar", json={"area_m2": 70, "estrato": 4, "localidad": "Suba"})
    assert "Estimación: 70.0 m², estrato 4, Suba" in _texto()
    r = c.post("/api/estimar", json={"area_m2": 5, "estrato": 4})
    assert r.status_code == 422
    assert "Datos inválidos en POST /api/estimar" in _texto()
    assert "area_m2" in registro.ERRORES.read_text(encoding="utf-8")


def test_errores_del_navegador_llegan_al_registro(cliente):  # noqa: F811
    c, _ = cliente
    r = c.post("/api/registro-cliente", json=[{"nivel": "error", "mensaje": "Error de JavaScript: x is undefined",
                                              "pagina": "/", "navegador": "Prueba/1.0", "sesion": "abc"}])
    assert r.status_code == 204
    assert "vivienda.navegador" in _texto()
    assert "x is undefined" in registro.ERRORES.read_text(encoding="utf-8")


def test_registros_y_diagnostico_solo_locales(cliente):  # noqa: F811
    c, _ = cliente
    r = c.get("/api/registros?n=50")
    assert r.status_code == 200 and "vivienda.log" in r.text
    d = c.get("/api/diagnostico").json()
    assert {"ok", "aviso", "error"} == set(d["resumen"])
    assert any(i["grupo"] == "modelo" and i["estado"] == "ok" for i in d["revisiones"])
    assert "Resultado:" in c.get("/api/diagnostico?texto=1").text


def test_error_inesperado_devuelve_500_con_rid_y_traza(cliente, monkeypatch):  # noqa: F811
    c, _ = cliente
    import app.main as principal

    def roto(*a, **k):
        raise ZeroDivisionError("división de prueba")
    monkeypatch.setattr(principal.estimar, "estimar", roto)
    r = c.post("/api/estimar", json={"area_m2": 70, "estrato": 4}, headers={"X-Request-ID": "rid-500"})
    assert r.status_code == 500
    assert r.json()["rid"] == "rid-500"
    errores = registro.ERRORES.read_text(encoding="utf-8")
    assert "división de prueba" in errores and "Traceback" in errores


def test_ultimas_lineas_lee_el_final():
    registro.configurar("prueba")
    logging.getLogger("vivienda.prueba").warning("marca-final-única")
    for h in logging.getLogger().handlers:
        h.flush()
    assert any("marca-final-única" in ln for ln in registro.ultimas_lineas(5))
