FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
# Al arrancar, la aplicación entrena el modelo si hace falta (ver app/main.py)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
