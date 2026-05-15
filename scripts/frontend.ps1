$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PlatformRoot = Resolve-Path (Join-Path $ScriptDir "..")
$FrontendRoot = Join-Path $PlatformRoot "frontend"

Set-Location $FrontendRoot

if (-not (Test-Path (Join-Path $FrontendRoot "node_modules"))) {
  npm install
}

npm run dev
