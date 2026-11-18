-- Esquema generado desde scraper/db.py (PostgreSQL) con sqlalchemy.schema.CreateTable/CreateIndex.
-- Las tablas e índices se crean solos al ejecutar el recolector (metadata.create_all).
-- En una base ya existente, create_all NO agrega índices nuevos: ejecute los CREATE INDEX a mano.

CREATE TABLE cambios (
	id SERIAL NOT NULL, 
	url_origen VARCHAR(500) NOT NULL, 
	fecha TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	campo VARCHAR(40) NOT NULL, 
	antes VARCHAR(160), 
	despues VARCHAR(160), 
	PRIMARY KEY (id)
);

CREATE INDEX ix_cambios_url_fecha ON cambios (url_origen, fecha);

CREATE TABLE dataset_versiones (
	version SERIAL NOT NULL, 
	fecha_corte TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	n_crudos INTEGER, 
	n_registros INTEGER, 
	checksum_sha256 VARCHAR(64), 
	reglas TEXT, 
	notas TEXT, 
	PRIMARY KEY (version)
);

CREATE TABLE ejecuciones (
	id SERIAL NOT NULL, 
	inicio TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	fin TIMESTAMP WITHOUT TIME ZONE, 
	resultado VARCHAR(40), 
	enlaces_nuevos INTEGER, 
	avisos_nuevos INTEGER, 
	revisados INTEGER, 
	con_cambios INTEGER, 
	retirados INTEGER, 
	verificados_listado INTEGER, 
	version_dataset INTEGER, 
	mensaje TEXT, 
	PRIMARY KEY (id)
);

CREATE TABLE enlaces (
	url VARCHAR(500) NOT NULL, 
	portal VARCHAR(40) NOT NULL, 
	descubierto_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	procesado BOOLEAN NOT NULL, 
	ultimo_estado VARCHAR(40), 
	ultima_visita TIMESTAMP WITHOUT TIME ZONE, 
	visitas INTEGER, 
	disponible BOOLEAN, 
	PRIMARY KEY (url)
);

CREATE INDEX ix_enlaces_pendientes ON enlaces (portal, procesado);

CREATE TABLE inmuebles_crudos (
	id SERIAL NOT NULL, 
	portal VARCHAR(40) NOT NULL, 
	url_origen VARCHAR(500) NOT NULL, 
	fecha_consulta DATE NOT NULL, 
	metodo_extraccion VARCHAR(40), 
	precio_txt VARCHAR(80), 
	area_txt VARCHAR(80), 
	habitaciones_txt VARCHAR(40), 
	banos_txt VARCHAR(40), 
	estrato_txt VARCHAR(40), 
	parqueaderos_txt VARCHAR(40), 
	antiguedad_txt VARCHAR(80), 
	administracion_txt VARCHAR(80), 
	localidad_txt VARCHAR(120), 
	barrio_txt VARCHAR(160), 
	tipo_txt VARCHAR(80), 
	piso_txt VARCHAR(40), 
	estado_txt VARCHAR(40), 
	latitud_txt VARCHAR(40), 
	longitud_txt VARCHAR(40), 
	publicado_txt VARCHAR(60), 
	primera_vez TIMESTAMP WITHOUT TIME ZONE, 
	verificado_en TIMESTAMP WITHOUT TIME ZONE, 
	actualizado_en TIMESTAMP WITHOUT TIME ZONE, 
	disponible BOOLEAN, 
	retirado_en TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	UNIQUE (url_origen)
);

CREATE TABLE inmuebles_limpios (
	id SERIAL NOT NULL, 
	crudo_id INTEGER NOT NULL, 
	portal VARCHAR(40) NOT NULL, 
	precio NUMERIC(16, 0) NOT NULL, 
	area_m2 NUMERIC(8, 2) NOT NULL, 
	habitaciones INTEGER, 
	banos INTEGER, 
	estrato INTEGER, 
	parqueaderos INTEGER, 
	localidad VARCHAR(60), 
	barrio VARCHAR(160), 
	antiguedad INTEGER, 
	administracion NUMERIC(12, 0), 
	piso INTEGER, 
	estado VARCHAR(20), 
	latitud NUMERIC(9, 6), 
	longitud NUMERIC(9, 6), 
	fecha_consulta DATE NOT NULL, 
	publicado DATE, 
	url_origen VARCHAR(500) NOT NULL, 
	version_dataset INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_limpios_version_crudo UNIQUE (version_dataset, crudo_id), 
	FOREIGN KEY(crudo_id) REFERENCES inmuebles_crudos (id), 
	FOREIGN KEY(version_dataset) REFERENCES dataset_versiones (version)
);

CREATE INDEX ix_limpios_version_localidad ON inmuebles_limpios (version_dataset, localidad);
