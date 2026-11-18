"""Modelo de datos. Funciona con PostgreSQL (proyecto) o SQLite (pruebas locales).

Tablas:
  enlaces          -> URLs de avisos descubiertas en la fase de listado (cola de trabajo)
  inmuebles_crudos -> lo que se extrajo de cada aviso, como texto, sin normalizar
  inmuebles_limpios-> registros que pasan las reglas de calidad (fuente del entrenamiento)
  dataset_versiones-> cortes congelados del dataset con su suma de verificación
  cambios          -> historial: cada cambio de precio, característica o disponibilidad de un aviso
  ejecuciones      -> cada ciclo automático, con sus conteos y su resultado
"""
import logging
import os
import pathlib

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
    # Seguimiento para el ciclo automático: cuándo se visitó y si el aviso sigue publicado
    Column("ultima_visita", DateTime),
    Column("visitas", Integer, default=0),
    Column("disponible", Boolean, default=True),
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
    Column("publicado_txt", String(60)),      # fecha de publicación según el portal, si la informa
    # Trazabilidad de cada aviso:
    Column("primera_vez", DateTime),          # cuándo lo encontró el recolector por primera vez
    Column("verificado_en", DateTime),        # última vez que se confirmó contra la fuente (listado o aviso)
    Column("actualizado_en", DateTime),       # última vez que cambió algún dato (o se creó)
    Column("disponible", Boolean, default=True),
    Column("retirado_en", DateTime),
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
    Column("publicado", Date),
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


cambios = Table(
    "cambios", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("url_origen", String(500), nullable=False),
    Column("fecha", DateTime, nullable=False, server_default=func.now()),
    Column("campo", String(40), nullable=False),     # precio, area, ..., disponible
    Column("antes", String(160)),
    Column("despues", String(160)),
)
Index("ix_cambios_url_fecha", cambios.c.url_origen, cambios.c.fecha)

ejecuciones = Table(
    "ejecuciones", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("inicio", DateTime, nullable=False),
    Column("fin", DateTime),
    Column("resultado", String(40)),                # completo | bloqueado | sin_portales | error
    Column("enlaces_nuevos", Integer, default=0),
    Column("avisos_nuevos", Integer, default=0),
    Column("revisados", Integer, default=0),
    Column("con_cambios", Integer, default=0),
    Column("retirados", Integer, default=0),
    Column("verificados_listado", Integer, default=0),   # confirmados desde el listado sin abrir el aviso
    Column("version_dataset", Integer),
    Column("mensaje", Text),
)

RAIZ = pathlib.Path(__file__).resolve().parents[1]
log = logging.getLogger("vivienda.db")


def url_por_defecto() -> str:
    (RAIZ / "datos").mkdir(exist_ok=True)
    return "sqlite:///" + (RAIZ / "datos" / "vivienda.db").as_posix()


def asegurar_columnas(eng) -> list[str]:
    """Migración mínima: agrega a las tablas existentes las columnas nuevas del modelo.

    create_all crea tablas que faltan, pero no toca las que ya existen. Una base creada con una
    versión anterior del recolector recibe aquí sus columnas nuevas (siempre opcionales), así que
    los datos ya recolectados se conservan. Para cambios más profundos, usar Alembic.
    """
    from sqlalchemy import inspect, text
    agregadas = []
    insp = inspect(eng)
    for tabla in metadata.sorted_tables:
        if not insp.has_table(tabla.name):
            continue
        existentes = {c["name"] for c in insp.get_columns(tabla.name)}
        for col in tabla.columns:
            if col.name not in existentes:
                tipo = col.type.compile(dialect=eng.dialect)
                with eng.begin() as c:
                    c.execute(text(f'ALTER TABLE {tabla.name} ADD COLUMN {col.name} {tipo}'))
                agregadas.append(f"{tabla.name}.{col.name}")
                log.info("Base de datos actualizada: columna nueva %s.%s (%s)", tabla.name, col.name, tipo)
    return agregadas


def motor(url: str | None = None):
    url = url or os.getenv("DATABASE_URL") or url_por_defecto()
    visible = url.split("@")[-1] if "@" in url else url        # sin usuario ni clave en el registro
    log.debug("Abriendo base de datos: %s", visible)
    try:
        eng = create_engine(url, future=True)
        metadata.create_all(eng)
        asegurar_columnas(eng)
    except Exception:
        log.exception("No se pudo abrir o preparar la base de datos %s", visible)
        raise
    return eng
