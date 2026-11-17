# Vivienda Bogotá: precio de apartamentos en venta

Aplicación web que estima el **precio de publicación** de un apartamento en venta en Bogotá D.C. y lo compara con avisos parecidos. Incluye todo el flujo del proyecto: recolección de avisos con web scraping controlado, limpieza y versionado del dataset, entrenamiento y comparación de modelos, y una interfaz para consultar el resultado.

Proyecto de Electiva II, Universidad ECCI: Diego Alejandro Galicia Forero, Cristian Javier Gómez Castañeda y Camila Andrea Vanegas Ducuara.

![Pantalla principal de la aplicación](docs/captura.png)

> La estimación es una referencia del precio al que se publican apartamentos similares. **No es un avalúo comercial.**

## Qué hace

- **Estimar:** se describe un apartamento (área, estrato, localidad, habitaciones, baños, parqueaderos, antigüedad) y la aplicación devuelve un precio, un rango probable, el precio por metro cuadrado, qué características suben o bajan el precio y los avisos más parecidos.
- **Mercado:** un diagrama de caja del precio por m² según estrato, la relación entre área y precio, el m² mediano por localidad y un mapa de los avisos.
- **Avisos:** una tabla con filtros para revisar los datos uno por uno.
- **Modelo:** las métricas de validación cruzada y de la prueba final, explicadas.

## Empezar en 3 minutos

Necesita Python 3.11 o superior.

```bash
git clone https://github.com/USUARIO/vivienda-bogota.git
cd vivienda-bogota
python -m venv .venv
source .venv/bin/activate          # Windows (PowerShell): .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app
```

Abra <http://127.0.0.1:8000>.

La primera vez, si no hay ningún dataset en `datos/`, la aplicación crea **datos sintéticos de demostración** y entrena el modelo con ellos. La interfaz lo indica con una franja amarilla. Esos datos los inventa un programa (`modelo/demo.py`) y no describen el mercado real: sirven solo para probar la aplicación.

### Con Docker

```bash
docker compose up --build
```

Y abra <http://localhost:8000>. Las carpetas `datos/` y `artefactos/` se comparten con el contenedor, así que los datasets que ponga en `datos/` los usa la aplicación.

## Usar datos reales

La aplicación busca el dataset en este orden:

1. la ruta en la variable `VIVIENDA_DATASET`, si está definida;
2. el corte más reciente `datos/dataset_vN.csv` que produce la limpieza;
3. los datos de demostración.

Cada vez que arranca compara la huella SHA-256 del dataset con la del último entrenamiento. Si cambió, vuelve a entrenar sola. También puede entrenar a mano:

```bash
python -m modelo.entrenar                              # usa el dataset que encuentre
python -m modelo.entrenar --dataset datos/dataset_v3.csv
```

El CSV necesita al menos las columnas `precio` y `area_m2`, y debería traer `estrato` y `localidad`. Las demás (`habitaciones`, `banos`, `parqueaderos`, `antiguedad`, `barrio`, `latitud`, `longitud`, `url_origen`) se aprovechan si existen. Es el mismo formato que produce `limpieza.construir_dataset`.

## Cómo funciona el modelo

`modelo/entrenar.py` aplica la regla de decisión del proyecto:

1. Separa el 20 % de los avisos como **prueba final**, que no se usa para elegir.
2. Compara con **validación cruzada de 5 partes**:
   - la **línea base**: precio mediano del m² en la misma localidad y estrato, multiplicado por el área (si hay menos de 3 avisos en esa combinación, usa el estrato; si tampoco, la ciudad);
   - un **árbol de decisión**, un **bosque aleatorio** y un **gradient boosting**, que aprenden el logaritmo del precio.
3. Elige el de menor **MAE**. Si ningún modelo de árboles supera a la línea base, se usa la línea base y la interfaz lo dice.
4. Mide el elegido una sola vez en la prueba final: MAE, RMSE, R², error relativo (MAE dividido por el precio mediano; la meta del proyecto es 15 %) y cobertura del rango.
5. Lo vuelve a entrenar con todos los datos y lo guarda en `artefactos/modelo.joblib`, con las métricas en `artefactos/metricas.json`.

**Rango de precio.** Se calcula con predicción conformal: con los errores que el modelo comete en avisos que no vio, se busca el ancho que contiene el precio real 8 de cada 10 veces.

**Explicación.** Para cada característica que el usuario informó, se calcula cuánto cambiaría la estimación si esa característica tomara el valor del apartamento típico del dataset. Es una aproximación: los efectos no se suman exactamente.

**Advertencias.** La aplicación avisa cuando un valor está fuera del rango que el modelo vio al entrenar, o cuando hay menos de 5 avisos de la localidad o del estrato elegidos.

Si tiene `mlflow` instalado, cada entrenamiento queda registrado en `mlruns/`.

## Recolectar datos reales

### Antes de extraer nada (obligatorio)

Revisión hecha el 4 de octubre de 2026:

| Portal | robots.txt | Decisión |
| --- | --- | --- |
| Finca Raíz | Permite `/venta/apartamentos/bogota/...` y los avisos individuales. Prohíbe `/venta/*-y-*`. | Utilizable si los términos de uso lo permiten |
| Ciencuadras | Permite `/inmueble/...`. Prohíbe `/search/` y `/site/`. | Utilizable si los términos de uso lo permiten |
| Metrocuadrado | **Prohíbe `/detail/` y `/metrocuadrado-results/`** | **No se extrae** (deshabilitado en la configuración) |

El robots.txt no reemplaza los términos de uso. Antes de recolectar, una persona debe leer los términos de cada portal. Si no prohíben la extracción automatizada, anota su nombre y la fecha en `config/portales.yaml` y cambia `terminos_revisados: true`. Mientras siga en `false`, el programa no se ejecuta. Ponga también un correo real de contacto en `user_agent`.

### Pasos

```bash
cp .env.example .env                                   # y cambie la clave
docker compose --profile recoleccion up -d db          # PostgreSQL (o use SQLite: ver .env.example)

python -m scraper.recolectar diagnostico --portal fincaraiz --url "<URL de un aviso>"
python -m scraper.recolectar listado --portal fincaraiz --paginas 10
python -m scraper.recolectar detalle --portal fincaraiz --max 200
python -m scraper.recolectar estado

python -m limpieza.geo descargar                       # mapa de localidades (una sola vez)
python -m limpieza.construir_dataset --notas "Corte Sprint 2"
```

Esto deja `datos/dataset_vN.csv` y `datos/reporte_calidad_vN.md`. Al reiniciar, la aplicación usa ese dataset y entrena con él.

El recolector respeta estas reglas:

- hace una pausa aleatoria de 4 a 8 segundos entre solicitudes, unos 500 avisos por hora como máximo;
- se **detiene** si el portal responde 403 o 429 o muestra un captcha, sin reintentar ni evadir el bloqueo;
- solo guarda los campos de una lista blanca y nunca lee nombres, teléfonos ni correos de anunciantes (Ley 1581 de 2012);
- excluye los avisos de `/proyectos-vivienda/`, que muestran un precio "desde" y no el de una unidad.

Antes de recolectar en volumen, pruebe con `diagnostico` sobre 2 o 3 avisos. Los portales cambian su HTML con frecuencia.

### Si un portal lo prohíbe: captura manual

Llene `carga_manual/plantilla_captura.csv` con un aviso por fila y ejecute:

```bash
python -m limpieza.cargar_manual carga_manual/plantilla_captura.csv
python -m limpieza.construir_dataset --notas "Incluye captura manual"
```

## Datos y privacidad

Los avisos recolectados **no se suben al repositorio**: `datos/` está en `.gitignore`, salvo la tabla `barrios_localidad.csv`. Cada persona que use el proyecto debe construir su propio dataset y cumplir los términos de cada portal. Antes de compartir un dataset, revise que no contenga datos personales.

## API

La documentación interactiva está en <http://127.0.0.1:8000/docs>.

| Método | Ruta | Para qué |
| --- | --- | --- |
| GET | `/api/estado` | Dataset en uso, modelo elegido y localidades válidas |
| POST | `/api/estimar` | Precio, rango, explicación y avisos parecidos |
| GET | `/api/mercado` | Datos de los gráficos del mercado |
| GET | `/api/avisos` | Avisos con filtros (`localidad`, `estrato`, `precio_max`, `area_min`, `orden`, `pagina`) |
| GET | `/api/modelo` | Métricas completas del último entrenamiento |

Ejemplo:

```bash
curl -X POST http://127.0.0.1:8000/api/estimar \
  -H "Content-Type: application/json" \
  -d '{"area_m2": 70, "estrato": 4, "localidad": "Suba", "habitaciones": 3, "banos": 2, "parqueaderos": 1}'
```

## Estructura

```
app/                 API (FastAPI) e interfaz web (HTML, CSS y JavaScript sin compilación)
  static/vendor/     Leaflet y fuentes incluidas, para no depender de CDN
modelo/              carga del dataset, línea base, entrenamiento, estimación y datos de demostración
scraper/             recolección: robots.txt, pausas, freno ante bloqueos, extracción
limpieza/            normalización, localidad por coordenadas, deduplicación, versionado
config/portales.yaml portales, URLs, pausas y control de términos de uso
sql/schema.sql       esquema de referencia en PostgreSQL
tests/               pruebas del recolector, la limpieza, el modelo y la API
```

## Pruebas

```bash
pytest -q
```

GitHub Actions las ejecuta en cada cambio (`.github/workflows/pruebas.yml`).

## Licencia

Código bajo licencia MIT (ver `LICENSE`). Leaflet usa licencia BSD-2 y las fuentes Bricolage Grotesque y Public Sans, SIL Open Font License.
