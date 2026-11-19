# Starts a throwaway local PostgreSQL 16 for development. Optional: skip this script
# if you already have PostgreSQL. It does not install anything system-wide.
#
#   .\scripts\dev-postgres.ps1          start (downloads ~315 MB the first time)
#   .\scripts\dev-postgres.ps1 -Stop    stop it
#
# Files live in .devpg\ (git-ignored). Listens on 127.0.0.1:5433 only.
param([switch]$Stop)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$root = Split-Path -Parent $PSScriptRoot
$base = Join-Path $root ".devpg"
$bin = Join-Path $base "pgsql\bin"
$data = Join-Path $base "data"
$port = 5433
$db = "insurance_navigator"

if ($Stop) {
    if (Test-Path (Join-Path $data "postmaster.pid")) { & "$bin\pg_ctl.exe" -D $data stop -m fast }
    else { Write-Host "Not running." }
    return
}

if (-not (Test-Path "$bin\initdb.exe")) {
    New-Item -ItemType Directory -Force $base | Out-Null
    $zip = Join-Path $base "pg.zip"
    Write-Host "Downloading portable PostgreSQL 16..."
    Invoke-WebRequest -UseBasicParsing "https://get.enterprisedb.com/postgresql/postgresql-16.9-1-windows-x64-binaries.zip" -OutFile $zip
    Expand-Archive $zip $base -Force
    Remove-Item $zip
}

if (-not (Test-Path (Join-Path $data "PG_VERSION"))) {
    # Local-only trust auth on 127.0.0.1 for this throwaway cluster.
    & "$bin\initdb.exe" -D $data -U postgres -A trust -E UTF8 | Out-Null
}

if (-not (Test-Path (Join-Path $data "postmaster.pid"))) {
    # Start without -Wait (it would wait for the server itself), then poll until ready.
    $pgArgs = @("-D", "`"$data`"", "-o", "`"-p $port -c listen_addresses=127.0.0.1`"", "-l", "`"$(Join-Path $base 'postgres.log')`"", "start")
    Start-Process -FilePath "$bin\pg_ctl.exe" -ArgumentList $pgArgs -WindowStyle Hidden
    $ready = $false
    for ($i = 0; $i -lt 30 -and -not $ready; $i++) {
        Start-Sleep -Seconds 1
        & "$bin\pg_isready.exe" -h 127.0.0.1 -p $port | Out-Null
        $ready = ($LASTEXITCODE -eq 0)
    }
    if (-not $ready) { throw "PostgreSQL did not start. See $base\postgres.log" }
}

$exists = & "$bin\psql.exe" -h 127.0.0.1 -p $port -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$db'"
if ($exists -ne "1") { & "$bin\createdb.exe" -h 127.0.0.1 -p $port -U postgres $db }

Write-Host "PostgreSQL is running."
Write-Host "DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:$port/$db"
