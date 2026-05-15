$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PlatformRoot = Resolve-Path (Join-Path $ScriptDir "..")
$WorkspaceRoot = Split-Path -Parent $PlatformRoot
$BackendRoot = Join-Path $PlatformRoot "backend"
$FreeCADHome = Join-Path $WorkspaceRoot "FreeCAD_1.1.1-Windows-x86_64-py311"
$PythonExe = Join-Path $FreeCADHome "bin\\python.exe"

if (-not (Test-Path $PythonExe)) {
  throw "FreeCAD embedded python not found: $PythonExe"
}

$env:FREECAD_MCP_FREECAD_PATH = $FreeCADHome
Set-Location $BackendRoot
& $PythonExe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
