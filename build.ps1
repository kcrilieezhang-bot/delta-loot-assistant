param(
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [switch]$InstallDependencies
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot

$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonExe)) {
    $pythonExe = (Get-Command python -ErrorAction Stop).Source
}

$version = & $pythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($version -ne "3.12") {
    throw "需要 Python 3.12，当前版本为 $version。"
}

if ($LASTEXITCODE -ne 0) { throw "Python version check failed." }
if ($InstallDependencies) {
    & $pythonExe -m pip install -e ".[dev,build]"
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
}
if (-not $SkipTests) {
    & $pythonExe -m ruff check --no-cache src tests packaging/entrypoint.py
    if ($LASTEXITCODE -ne 0) { throw "Ruff checks failed." }
    & $pythonExe -m pytest -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { throw "Tests failed." }
}

$workspaceRoot = Split-Path -Parent $projectRoot
$releaseRoot = Join-Path $workspaceRoot "app"
$buildCache = Join-Path $workspaceRoot "_archive\build-cache"
& $pythonExe -m PyInstaller --noconfirm --distpath $releaseRoot --workpath $buildCache packaging\delta_loot_assistant.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

if (-not $SkipInstaller) {
    $isccCandidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    )
    $iscc = $isccCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $iscc) {
        throw "Inno Setup 6 not found. Portable app: $releaseRoot\DeltaLootAssistant"
    }
    & $iscc packaging\installer.iss
    if ($LASTEXITCODE -ne 0) { throw "Installer build failed." }
}

Write-Host "构建完成。"
