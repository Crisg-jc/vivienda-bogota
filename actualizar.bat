@echo off
REM Doble clic para instalar la version nueva desde el zip de Descargas (sin escribir comandos).
REM Conserva config\portales.yaml y toda la carpeta datos (base, registros, modelos).
chcp 65001 >nul
setlocal EnableDelayedExpansion

REM Este archivo tambien se reemplaza al descomprimir: se copia a TEMP y sigue desde alli.
if not "%~1"=="--desde-temp" (
  copy /y "%~f0" "%TEMP%\vivienda-actualizar.bat" >nul
  "%TEMP%\vivienda-actualizar.bat" --desde-temp "%~dp0"
  exit /b
)
set "CARPETA=%~2"
if "%CARPETA:~-1%"=="\" set "CARPETA=%CARPETA:~0,-1%"
for %%I in ("%CARPETA%\..") do set "PADRE=%%~fI"
set "REG=%CARPETA%\datos\registros\actualizacion.log"
if not exist "%CARPETA%\datos\registros" mkdir "%CARPETA%\datos\registros"

echo Buscando el zip mas reciente de vivienda-bogota en Descargas...
set "ZIP="
for /f "delims=" %%F in ('dir /b /o-d "%USERPROFILE%\Downloads\vivienda-bogota*.zip" 2^>nul') do (
  if not defined ZIP set "ZIP=%USERPROFILE%\Downloads\%%F"
)
if not defined ZIP (
  echo [ERROR] No hay ningun vivienda-bogota*.zip en %USERPROFILE%\Downloads. Descarguelo y vuelva a intentar.
  echo %date% %time% [ERROR] sin zip en Descargas>> "%REG%"
  pause
  exit /b 1
)
echo Zip: !ZIP!
echo %date% %time% Actualizando desde !ZIP!>> "%REG%"

echo.
echo Cierre la ventana donde corre la aplicacion (Ctrl+C) si esta abierta, y presione una tecla.
pause >nul

echo Deteniendo el recolector en segundo plano...
if exist "%CARPETA%\.venv\Scripts\python.exe" (
  pushd "%CARPETA%"
  ".venv\Scripts\python.exe" -m scraper.servicio --detener >nul 2>&1
  popd
  timeout /t 6 /nobreak >nul
)

echo Guardando su configuracion...
copy /y "%CARPETA%\config\portales.yaml" "%TEMP%\vivienda-portales.yaml" >nul

echo Descomprimiendo...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath $env:ZIP -DestinationPath $env:PADRE -Force"
if errorlevel 1 (
  echo [ERROR] No se pudo descomprimir. Revise que el zip no este danado y que ningun archivo este abierto.
  echo %date% %time% [ERROR] Expand-Archive fallo>> "%REG%"
  copy /y "%TEMP%\vivienda-portales.yaml" "%CARPETA%\config\portales.yaml" >nul
  pause
  exit /b 1
)
copy /y "%TEMP%\vivienda-portales.yaml" "%CARPETA%\config\portales.yaml" >nul
echo %date% %time% Actualizado>> "%REG%"
echo Listo. Abriendo la aplicacion...
cd /d "%CARPETA%"
call "%CARPETA%\iniciar.bat"
