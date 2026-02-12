param(
    [Parameter(Mandatory = $true)]
    [string]$RepoUrl,

    [string]$Branch = "main",
    [string]$TargetDir = "C:\FinanceBot"
)

$ErrorActionPreference = "Stop"

Write-Host "[Deploy] Target: $TargetDir"
if (!(Test-Path $TargetDir)) {
    New-Item -ItemType Directory -Path $TargetDir | Out-Null
}

if (!(Test-Path (Join-Path $TargetDir ".git"))) {
    Write-Host "[Deploy] Clonando repositorio..."
    git clone --branch $Branch $RepoUrl $TargetDir
} else {
    Write-Host "[Deploy] Atualizando repositorio..."
    git -C $TargetDir fetch origin $Branch
    git -C $TargetDir checkout $Branch
    git -C $TargetDir pull --ff-only origin $Branch
}

$venvDir = Join-Path $TargetDir ".venv"
$pythonExe = Join-Path $venvDir "Scripts\python.exe"

if (!(Test-Path $pythonExe)) {
    Write-Host "[Deploy] Criando ambiente virtual..."
    python -m venv $venvDir
}

Write-Host "[Deploy] Instalando dependencias..."
& $pythonExe -m pip install --upgrade pip
& $pythonExe -m pip install --upgrade setuptools wheel
& $pythonExe -m pip install -r (Join-Path $TargetDir "requirements.txt")
& $pythonExe -m pip install --force-reinstall --no-cache-dir greenlet playwright
& $pythonExe -m playwright install chromium

Write-Host "[Deploy] Iniciando FinanceBot em modo servidor..."
Start-Process -FilePath $pythonExe -WorkingDirectory $TargetDir -ArgumentList "main.py --server --no-browser"

Write-Host "[Deploy] Concluido"
