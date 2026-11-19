"""Recolector en segundo plano: latido, pedido de ciclo y la app que solo muestra su estado."""
import datetime as dt
import json

from scraper import ciclo as cy
from scraper import servicio as sv


def test_latido_vivo_y_viejo(tmp_path, monkeypatch):
    monkeypatch.setattr(sv, "LATIDO", tmp_path / "servicio.json")
    assert not sv.vivo()
    r = sv.Recolector()
    monkeypatch.setattr(cy, "CARPETA", tmp_path)
    r.latir(estado="esperando")
    assert sv.vivo()
    viejo = json.loads(sv.LATIDO.read_text())
    viejo["latido"] = (dt.datetime.now() - dt.timedelta(minutes=10)).isoformat()
    sv.LATIDO.write_text(json.dumps(viejo))
    assert not sv.vivo(), "un recolector que no late hace 10 min se da por muerto"


def test_pedido_de_ciclo_despierta_la_espera(tmp_path, monkeypatch):
    monkeypatch.setattr(cy, "CARPETA", tmp_path)
    monkeypatch.setattr(sv, "LATIDO", tmp_path / "servicio.json")
    monkeypatch.setattr(sv, "PEDIDO", tmp_path / "pedido.json")
    monkeypatch.setattr(cy, "PARAR", tmp_path / "parar")
    r = sv.Recolector()
    sv.pedir_ciclo()
    inicio = dt.datetime.now()
    r.esperar(3600, "prueba")
    assert (dt.datetime.now() - inicio).total_seconds() < 2 and not sv.PEDIDO.exists()
    sv.detener_recolector()
    r.esperar(3600, "prueba")
    assert r.parar.is_set()
