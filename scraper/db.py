"""Modelo de datos. Funciona con PostgreSQL (proyecto) o SQLite (pruebas locales).

Tablas:
  enlaces          -> URLs de avisos descubiertas en la fase de listado (cola de trabajo)
  inmuebles_crudos -> lo que se extrajo de cada aviso, como texto, sin normalizar
  inmuebles_limpios-> registros que pasan las reglas de calidad (fuente del entrenamiento)
  dataset_versiones-> cortes congelados del dataset con su suma de verificación
"""
import os

from dotenv import load_dotenv
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
)

load_dotenv()

metadata = MetaData()

enlaces = Table(
    "enlaces", metadata,
    Column("url", String(500), primary_key=True),
    Column("portal", String(40), nullable=False),
    Column("descubierto_en", DateTime, server_default=func.now()),
    Column("procesado", Boolean, nullable=False, default=False),
    Column("ultimo_estado", String(40)),
)

inmuebles_crudos = Table(
    "inmuebles_crudos", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("portal", String(40), nullable=False),
    Column("url_origen", String(500), nullable=False, unique=True),
    Column("fecha_consulta", Date, nullable=False),
    Column("metodo_extraccion", String(40)),   # json-ld | next-data | texto
    Column("precio_txt", String(80)),
    Column("area_txt", String(80)),
    Column("habitaciones_txt", String(40)),
    Column("banos_txt", String(40)),
    Column("estrato_txt", String(40)),
    Column("parqueaderos_txt", String(40)),
    Column("antiguedad_txt", String(80)),
    Column("administracion_txt", String(80)),
    Column("localidad_txt", String(120)),
    Column("barrio_txt", String(160)),
    Column("tipo_txt", String(80)),
    Column("piso_txt", String(40)),
    Column("estado_txt", String(40)),
    Column("latitud_txt", String(40)),
    Column("longitud_txt", String(40)),
)

inmuebles_limpios = Table(
    "inmuebles_limpios", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("crudo_id", Integer, ForeignKey("inmuebles_crudos.id"), nullable=False),
    Column("portal", String(40), nullable=False),
    Column("precio", Numeric(16, 0), nullable=False),
    Column("area_m2", Numeric(8, 2), nullable=False),
    Column("habitaciones", Integer),
    Column("banos", Integer),
    Column("estrato", Integer),
    Column("parqueaderos", Integer),
    Column("localidad", String(60)),
    Column("barrio", String(160)),
    Column("antiguedad", Integer),
    Column("administracion", Numeric(12, 0)),
    Column("piso", Integer),
    Column("estado", String(20)),           # usado | nuevo
    Column("latitud", Numeric(9, 6)),
    Column("longitud", Numeric(9, 6)),
    Column("fecha_consulta", Date, nullable=False),
    Column("url_origen", String(500), nullable=False),
    Column("version_dataset", Integer, ForeignKey("dataset_versiones.version"), nullable=False),
    # Un aviso crudo aparece una sola vez por versión del dataset.
    UniqueConstraint("version_dataset", "crudo_id", name="uq_limpios_version_crudo"),
)
# Consultas frecuentes: el recolector pide "enlaces pendientes de este portal" en cada ejecución,
# y el análisis filtra los limpios por versión y localidad.
Index("ix_enlaces_pendientes", enlaces.c.portal, enlaces.c.procesado)
Index("ix_limpios_version_localidad", inmuebles_limpios.c.version_dataset, inmuebles_limpios.c.localidad)

dataset_versiones = Table(
    "dataset_versiones", metadata,
    Column("version", Integer, primary_key=True),
    Column("fecha_corte", DateTime, server_default=func.now()),
    Column("n_crudos", Integer),
    Column("n_registros", Integer),
    Column("checksum_sha256", String(64)),
    Column("reglas", Text),     # JSON con las reglas y el conteo de descartes por regla
    Column("notas", Text),
)


def motor(url: str | None = None):
    url = url or os.getenv("DATABASE_URL", "sqlite:///datos/vivienda.db")
    eng = create_engine(url, future=True)
    metadata.create_all(eng)
    return eng
