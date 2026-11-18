"""Prepara el equipo antes de abrir la aplicación (lo llama iniciar.bat / iniciar.sh).

1. Instala o actualiza las librerías solo si requirements.txt cambió desde la última vez.
2. Si no hay base de datos en datos/ pero existe la del recolector anterior
   (Descargas/vivienda_scraper/datos/vivienda.db), la copia para no perder lo ya recolectado.
3. Ejecuta el diagnóstico rápido (sin internet) y deja todo en datos/registros/instalacion.log.
"""
import hashlib
import logging
import pathlib
import shutil
import subprocess
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
REGISTROS = RAIZ / "datos" / "registros"
REGISTROS.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s [preparar] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                    handlers=[logging.StreamHandler(sys.stdout),
                              logging.FileHandler(REGISTROS / "instalacion.log", encoding="utf-8")])
log = logging.getLogger("preparar")


def librerias():
    req = RAIZ / "requirements.txt"
    huella = hashlib.sha256(req.read_bytes()).hexdigest()
    marca = pathlib.Path(sys.prefix) / "vivienda-requisitos.sha256"
    if marca.exists() and marca.read_text().strip() == huella:
        log.info("Librerías al día (requirements.txt no cambió)")
        return True
    log.info("Instalando librerías (la primera vez tarda unos minutos)...")
    for cmd in ([sys.executable, "-m", "pip", "install", "--upgrade", "pip", "-q"],
                [sys.executable, "-m", "pip", "install", "-r", str(req), "--disable-pip-version-check"]):
        log.info("Ejecutando: %s", " ".join(cmd[1:]))
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        for linea in (r.stdout + r.stderr).splitlines():
            if linea.strip():
                (log.error if "ERROR" in linea else log.debug)("pip: %s", linea)
        if r.returncode != 0:
            log.error("pip terminó con código %s. Revise internet. Detalle completo en %s",
                      r.returncode, REGISTROS / "instalacion.log")
            (REGISTROS / "pip-ultimo-error.txt").write_text(r.stdout + "\n" + r.stderr, encoding="utf-8")
            return False
    marca.write_text(huella)
    log.info("Librerías instaladas")
    return True


def datos_anteriores():
    destino = RAIZ / "datos" / "vivienda.db"
    if destino.exists():
        log.info("Base de datos: %s (%.1f MB)", destino, destino.stat().st_size / 1e6)
        return
    candidatos = [pathlib.Path.home() / "Downloads" / "vivienda_scraper" / "datos" / "vivienda.db",
                  pathlib.Path.home() / "Descargas" / "vivienda_scraper" / "datos" / "vivienda.db",
                  pathlib.Path.home() / "Documents" / "vivienda_scraper" / "datos" / "vivienda.db"]
    for c in candidatos:
        if c.exists():
            shutil.copy2(c, destino)
            log.info("Se copió la base del recolector anterior: %s -> %s (%.1f MB). El original queda intacto.",
                     c, destino, c.stat().st_size / 1e6)
            return
    log.info("No hay base de datos todavía; buscada también en: %s", ", ".join(str(c) for c in candidatos))


def main():
    log.info("==== Preparando %s con Python %s ====", RAIZ, sys.version.split()[0])
    if sys.version_info < (3, 11):
        log.error("Se necesita Python 3.11 o más reciente; este es %s", sys.version.split()[0])
        return 1
    if not librerias():
        return 1
    try:
        datos_anteriores()
    except Exception:
        log.exception("No se pudo revisar o copiar la base de datos anterior (se sigue sin ella)")
    r = subprocess.run([sys.executable, "-m", "app.diagnostico", "--sin-red"], cwd=RAIZ)
    if r.returncode != 0:
        log.warning("El diagnóstico encontró errores (arriba, marcados [ERROR]). Se intenta abrir la app de todos modos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
