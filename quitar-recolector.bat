@echo off
REM Detiene el recolector en segundo plano y quita su inicio automatico con Windows.
REM La base de datos y lo ya recolectado no se tocan. Para volver a activarlo: iniciar.bat
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\python.exe" -m scraper.servicio --detener
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Vivienda Bogota - recolector.lnk" 2>nul
echo Inicio automatico quitado. Para que no vuelva a arrancar al abrir la app, ponga
echo   segundo_plano: false
echo en la seccion automatico de config\portales.yaml.
pause
