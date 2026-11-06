# Windows equivalent of `make run`: create venv, install, run the pipeline.
# Usage:  .\run.ps1            (pipeline)
#         .\run.ps1 test       (test suite)
param([string]$Target = "run")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
if (-not (Test-Path ".venv\Scripts\wb-pipeline.exe")) {
    & $py -m venv .venv
    & .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    & .venv\Scripts\python.exe -m pip install --quiet -e ".[dev]" -c constraints.txt
}
$env:PYTHONIOENCODING = "utf-8"

switch ($Target) {
    "run"  { & .venv\Scripts\wb-pipeline.exe run }
    "test" { & .venv\Scripts\python.exe -m pytest }
    default { throw "unknown target '$Target' (use: run | test)" }
}
exit $LASTEXITCODE
