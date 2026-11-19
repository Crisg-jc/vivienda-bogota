# Actualiza Vivienda Bogotá desde el zip más reciente de Descargas, sin doble clic.
# Uso (PowerShell):  powershell -ExecutionPolicy Bypass -File herramientas\actualizar.ps1
#
# 1. Busca la carpeta del proyecto con la base de datos más grande (la que tiene sus avisos).
# 2. Detiene el recolector y la aplicación de ESA carpeta.
# 3. Copia el código nuevo encima, sin tocar la carpeta datos ni config\portales.yaml.
# 4. Si config\portales.yaml perdió la autorización (por una actualización anterior que la pisó),
#    la recupera de una copia que la persona misma había dejado con terminos_revisados: true.
# 5. Abre la aplicación.
$ErrorActionPreference = "Stop"

Write-Host "Buscando su base de datos (puede tardar un poco)..."
$bases = Get-ChildItem -Path $HOME -Recurse -Depth 6 -Filter vivienda.db -ErrorAction SilentlyContinue |
  Where-Object { $_.Directory.Name -eq "datos" } | Sort-Object Length -Descending
if (-not $bases) { throw "No encontré ninguna carpeta del proyecto con datos\vivienda.db en $HOME." }
$bases | Select-Object @{n="Carpeta"; e={$_.Directory.Parent.FullName}}, @{n="MB"; e={[math]::Round($_.Length/1MB,1)}}, LastWriteTime | Format-Table -AutoSize
$carpeta = $bases[0].Directory.Parent.FullName
Write-Host "Se actualiza la carpeta con la base más grande: $carpeta" -ForegroundColor Green

$zip = Get-ChildItem "$HOME\Downloads\vivienda-bogota*.zip" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $zip) { throw "No hay ningún vivienda-bogota*.zip en $HOME\Downloads." }
Write-Host "Zip: $($zip.FullName) ($($zip.LastWriteTime))"

Write-Host "Deteniendo el recolector y la aplicación de esa carpeta..."
$py = Join-Path $carpeta ".venv\Scripts\python.exe"
if (Test-Path $py) {
  Push-Location $carpeta
  try { & $py -m scraper.servicio --detener 2>&1 | Out-Null } catch { Write-Host "  (el recolector no estaba corriendo)" }
  Pop-Location
}
Get-Process python*, pythonw* -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$carpeta*" } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

$config = Join-Path $carpeta "config\portales.yaml"
$respaldo = Join-Path $env:TEMP "vivienda-portales-$(Get-Date -Format yyyyMMdd-HHmmss).yaml"
if (Test-Path $config) { Copy-Item $config $respaldo; Write-Host "Copia de su configuración: $respaldo" }

Write-Host "Descomprimiendo..."
$tmp = Join-Path $env:TEMP "vivienda-nueva"
if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
Expand-Archive -LiteralPath $zip.FullName -DestinationPath $tmp -Force
$origen = Join-Path $tmp "vivienda-bogota"
# /E copia subcarpetas; /XD datos .venv: nunca se tocan; /XF portales.yaml: la configuración es suya.
robocopy $origen $carpeta /E /XD datos .venv /XF portales.yaml /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy falló (código $LASTEXITCODE)." }
Remove-Item $tmp -Recurse -Force

# La autorización solo se recupera de una copia suya que ya la tenía; nunca se inventa.
$autorizado = { param($f) (Test-Path $f) -and (Select-String -Path $f -Pattern '^\s*terminos_revisados:\s*true' -Quiet) }
if (-not (& $autorizado $config)) {
  $copias = @(Get-ChildItem $env:TEMP -Filter "vivienda-portales*.yaml" -ErrorAction SilentlyContinue) +
            @(Get-ChildItem (Join-Path $carpeta "config") -Filter "portales*.yaml" -ErrorAction SilentlyContinue) +
            @($bases | ForEach-Object { Get-ChildItem (Join-Path $_.Directory.Parent.FullName "config") -Filter "portales*.yaml" -ErrorAction SilentlyContinue })
  $buena = $copias | Where-Object { $_.Name -ne "portales.ejemplo.yaml" -and $_.FullName -ne $config -and (& $autorizado $_.FullName) } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if ($buena) {
    if (Test-Path $config) { Copy-Item $config "$config.sin-autorizacion" -Force }
    Copy-Item $buena.FullName $config -Force
    Write-Host "Autorización recuperada de su copia: $($buena.FullName)" -ForegroundColor Green
  } else {
    Write-Host "No encontré ninguna copia suya con terminos_revisados: true." -ForegroundColor Yellow
    Write-Host "Si ya revisó los términos del portal, póngalo en $config (se abre ahora)."
    if (-not (Test-Path $config)) { Copy-Item (Join-Path $carpeta "config\portales.ejemplo.yaml") $config }
    notepad $config
  }
}

Write-Host "Abriendo la aplicación..." -ForegroundColor Green
Set-Location $carpeta
cmd /c iniciar.bat
