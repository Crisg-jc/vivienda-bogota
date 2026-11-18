@echo off
REM Doble clic para instalar (la primera vez) y abrir la aplicacion.
REM Con mas detalle en el registro:  iniciar.bat --depurar
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist "datos\registros" mkdir "datos\registros"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY (
  echo.
  echo [ERROR] No se encontro Python. Instalelo desde https://www.python.org/downloads/
  echo         y marque la casilla "Add python.exe to PATH". Luego vuelva a abrir este archivo.
  echo %date% %time% [ERROR] Python no instalado>> "datos\registros\instalacion.log"
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creando el entorno de Python en .venv ...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [ERROR] No se pudo crear .venv. Detalle en datos\registros\instalacion.log
    echo %date% %time% [ERROR] fallo python -m venv>> "datos\registros\instalacion.log"
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" herramientas\preparar.py
if errorlevel 1 (
  echo.
  echo [ERROR] La preparacion fallo. Revise datos\registros\instalacion.log
  pause
  exit /b 1
)

echo.
echo Abriendo la aplicacion. Para cerrarla: Ctrl+C en esta ventana.
echo Registros: datos\registros\vivienda.log  y  datos\registros\errores.log
echo.
".venv\Scripts\python.exe" -m app --abrir %*
if errorlevel 1 (
  echo.
  echo [ERROR] La aplicacion se cerro con un error. Revise datos\registros\errores.log
  pause
)
endlocal
