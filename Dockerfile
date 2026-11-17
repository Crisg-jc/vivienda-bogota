FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# El proceso no corre como root: si alguien lograra ejecutar código en él, no controla el contenedor.
RUN useradd --create-home --uid 10001 vivienda && mkdir -p datos artefactos && chown -R vivienda:vivienda datos artefactos
USER vivienda

EXPOSE 8000
# El contenedor se marca "sano" solo cuando el dataset y el modelo están cargados.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/salud', timeout=4).status == 200 else 1)"

# Un solo proceso: el modelo vive en memoria y el arranque puede entrenar. Ver README, "Escalar".
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
