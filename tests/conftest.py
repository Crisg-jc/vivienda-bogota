"""Las pruebas escriben su registro en una carpeta temporal, no en datos/registros del proyecto."""
import os
import tempfile

os.environ.setdefault("VIVIENDA_REGISTROS", tempfile.mkdtemp(prefix="vivienda-registros-"))
