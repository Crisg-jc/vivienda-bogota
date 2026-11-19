"""Las pruebas escriben su registro en una carpeta temporal, no en datos/registros del proyecto."""
import os
import tempfile

os.environ.setdefault("VIVIENDA_REGISTROS", tempfile.mkdtemp(prefix="vivienda-registros-"))
os.environ.setdefault("VIVIENDA_ESTADO", tempfile.mkdtemp(prefix="vivienda-estado-"))

import pathlib  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _sin_corte_de_evaluacion(monkeypatch, request):
    """Las pruebas controlan sus propios datos: el corte real que trae el repositorio (muestra/) queda
    fuera salvo en las que lo piden con la marca `con_muestra`."""
    if request.node.get_closest_marker("con_muestra"):
        return
    from modelo import datos
    monkeypatch.setattr(datos, "MUESTRA", pathlib.Path("/no/existe/dataset_corte.csv"))
