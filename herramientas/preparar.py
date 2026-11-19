"""Prepara el equipo antes de abrir la aplicación (lo llama iniciar.bat / iniciar.sh).

1. Instala o actualiza las librerías solo si requirements.txt cambió desde la última vez.
2. Si no hay base de datos en datos/ pero existe la del recolector anterior
   (Descargas/vivienda_scraper/datos/vivienda.db), la copia para no perder lo ya recolectado.
3. Ejecuta el diagnóstico rápido (sin internet) y deja todo en datos/registros/instalacion.log.
"""
import hashlib
import logging
import os
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


def actualizar_config():
    """config/portales.yaml de una versión anterior: se actualizan solo los valores de verificación
    que siguen en su valor viejo por defecto (los que alguien cambió a propósito se respetan).
    Los portales, el correo y la autorización de términos no se tocan. Queda copia del original."""
    import re

    from scraper.recolectar import asegurar_conf
    ruta = asegurar_conf()
    if not ruta.exists():
        return
    texto = ruta.read_text(encoding="utf-8")
    if re.search(r"^version_config:\s*2", texto, re.M):
        log.info("config/portales.yaml al día")
        return
    copia = ruta.with_name("portales.antes-de-v2.yaml")
    copia.write_text(texto, encoding="utf-8")
    cambios = []
    for clave, viejo, nuevo in (("intervalo_horas", "12", "2"), ("dias_entre_revisiones", "7", "3"),
                                ("revisiones_por_ciclo", "300", "400")):
        patron = re.compile(rf"^(\s+{clave}:\s*){viejo}\b", re.M)
        if patron.search(texto):
            texto = patron.sub(rf"\g<1>{nuevo}", texto)
            cambios.append(f"{clave} {viejo} -> {nuevo}")
    if "dias_vigencia" not in texto:
        texto = re.sub(r"^(\s+)(dias_entre_revisiones:[^\n]*\n)", r"\1\2\1dias_vigencia: 7\n", texto, count=1, flags=re.M)
        cambios.append("dias_vigencia: 7 (nuevo)")
    texto = "version_config: 2   # la usa herramientas/preparar.py para actualizar configuraciones viejas\n" + texto
    ruta.write_text(texto, encoding="utf-8")
    log.info("config/portales.yaml actualizado (copia del anterior en %s): %s", copia.name, "; ".join(cambios) or "sin cambios de valores")


def inicio_automatico():
    """Windows: acceso directo en la carpeta Inicio del usuario, para que el recolector arranque solo
    al encender el computador e iniciar sesión, aunque nadie abra la aplicación. No pide permisos
    de administrador. Se quita con quitar-recolector.bat."""
    if os.name != "nt":
        return
    import yaml
    try:
        aj = (yaml.safe_load((RAIZ / "config" / "portales.yaml").read_text(encoding="utf-8")) or {}).get("automatico") or {}
    except Exception:
        aj = {}
    if aj.get("segundo_plano", True) is False or aj.get("activo", True) is False:
        log.info("Recolector en segundo plano desactivado en config/portales.yaml: no se configura el inicio automático")
        return
    try:
        portales = (yaml.safe_load((RAIZ / "config" / "portales.yaml").read_text(encoding="utf-8")) or {}).get("portales") or {}
    except Exception:
        portales = {}
    if not any(p.get("habilitado") and p.get("terminos_revisados") for p in portales.values() if isinstance(p, dict)):
        # Una instalación para probar la app no deja nada arrancando con Windows.
        log.info("Ningún portal autorizado todavía: no se configura el inicio automático del recolector")
        return
    inicio = pathlib.Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    if not inicio.exists():
        log.warning("No se encontró la carpeta Inicio de Windows (%s): el recolector arrancará al abrir la app", inicio)
        return
    acceso = inicio / "Vivienda Bogota - recolector.lnk"
    pythonw = pathlib.Path(sys.executable).with_name("pythonw.exe")
    destino = pythonw if pythonw.exists() else pathlib.Path(sys.executable)
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:ACCESO);"
          "$s.TargetPath=$env:DESTINO;$s.Arguments='-m scraper.servicio';$s.WorkingDirectory=$env:CARPETA;"
          "$s.WindowStyle=7;$s.Description='Recolector de avisos de vivienda-bogota';$s.Save()")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "ACCESO": str(acceso), "DESTINO": str(destino), "CARPETA": str(RAIZ)})
    if r.returncode == 0 and acceso.exists():
        log.info("Inicio automático configurado: el recolector arranca solo al iniciar sesión en Windows (%s)", acceso)
    else:
        log.warning("No se pudo crear el acceso directo de inicio automático: %s", (r.stderr or r.stdout).strip()[:300])


def acceso_escritorio():
    """Windows: ícono "Vivienda Bogotá" en el escritorio que abre la aplicación (iniciar.bat)."""
    if os.name != "nt":
        return
    ps = ("$d=[Environment]::GetFolderPath('Desktop');$p=Join-Path $d 'Vivienda Bogotá.lnk';"
          "if(Test-Path $p){'ya'}else{$s=(New-Object -ComObject WScript.Shell).CreateShortcut($p);"
          "$s.TargetPath=$env:DESTINO;$s.WorkingDirectory=$env:CARPETA;$s.IconLocation=$env:ICONO;"
          "$s.Description='Estimar el precio de un apartamento en Bogotá';$s.Save();'creado'}")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "DESTINO": str(RAIZ / "iniciar.bat"), "CARPETA": str(RAIZ),
                            "ICONO": str(RAIZ / "herramientas" / "icono.ico")})
    salida = (r.stdout or "").strip()
    if r.returncode == 0 and salida == "creado":
        log.info("Ícono 'Vivienda Bogotá' creado en el escritorio: la próxima vez basta con doble clic")
    elif r.returncode != 0:
        log.warning("No se pudo crear el ícono del escritorio: %s", (r.stderr or "").strip()[:300])


def main():
    log.info("==== Preparando %s con Python %s ====", RAIZ, sys.version.split()[0])
    if sys.version_info < (3, 11):
        log.error("Se necesita Python 3.11 o más reciente; este es %s", sys.version.split()[0])
        return 1
    if not librerias():
        return 1
    try:
        actualizar_config()
    except Exception:
        log.exception("No se pudo actualizar config/portales.yaml (se sigue con el actual)")
    try:
        acceso_escritorio()
    except Exception:
        log.exception("No se pudo crear el ícono del escritorio (se sigue sin él)")
    try:
        inicio_automatico()
    except Exception:
        log.exception("No se pudo configurar el inicio automático del recolector (se sigue sin él)")
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
