-- Esquema generado desde scraper/db.py (PostgreSQL). Las tablas se crean solas al ejecutar el scraper.

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

CREATE TABLE enlaces (
	url VARCHAR(500) NOT NULL, 
	portal VARCHAR(40) NOT NULL, 
	descubierto_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	procesado BOOLEAN NOT NULL, 
	ultimo_estado VARCHAR(40), 
	PRIMARY KEY (url)
);

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
	fecha_consulta DATE NOT NULL, 
	url_origen VARCHAR(500) NOT NULL, 
	version_dataset INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(crudo_id) REFERENCES inmuebles_crudos (id), 
	FOREIGN KEY(version_dataset) REFERENCES dataset_versiones (version)
);
