"""Arranque rápido:  python -m app  [--host 0.0.0.0] [--port 8000]"""
import argparse

import uvicorn

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--port", type=int, default=8000)
ap.add_argument("--reload", action="store_true")
a = ap.parse_args()
print(f"Abra http://{'127.0.0.1' if a.host == '0.0.0.0' else a.host}:{a.port} en el navegador.")
uvicorn.run("app.main:app", host=a.host, port=a.port, reload=a.reload)
