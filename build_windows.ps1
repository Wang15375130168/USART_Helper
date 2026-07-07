param(
    [switch]$Installer,
    [switch]$SkipVenv,
    [switch]$NoInstall,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppName = "USART Helper"
$SpecPath = Join-Path $ProjectRoot "USART_Helper.spec"
$VenvDir = Join-Path $ProjectRoot ".venv-build"
$DistDir = Join-Path $ProjectRoot "dist"
$BuildDir = Join-Path $ProjectRoot "build"
$AppExe = Join-Path $DistDir "$AppName\$AppName.exe"

function Write-Step($Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Resolve-Iscc {
    $command = Get-Command "iscc" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
        "${env:LocalAppData}\Programs\Inno Setup 6\ISCC.exe"
    )

    foreach ($path in $candidates) {
        if ($path -and (Test-Path -LiteralPath $path)) {
            return $path
        }
    }

    return $null
}

function Stop-PackagedAppFromDist {
    $distRoot = [System.IO.Path]::GetFullPath($DistDir)
    $candidates = Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessName -eq $AppName }

    foreach ($process in $candidates) {
        $path = $null
        try {
            $path = $process.Path
        } catch {
            $path = $null
        }

        if (-not $path) {
            continue
        }

        $fullPath = [System.IO.Path]::GetFullPath($path)
        if (-not $fullPath.StartsWith($distRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            continue
        }

        Write-Host "Closing running packaged app: $fullPath" -ForegroundColor Yellow
        try {
            $process.CloseMainWindow() | Out-Null
            if (-not $process.WaitForExit(3000)) {
                Stop-Process -Id $process.Id -Force
                $process.WaitForExit(3000)
            }
        } catch {
            throw "Could not close running app process '$fullPath'. Close USART Helper manually and rerun the build."
        }
    }
}

function Remove-PathWithRetry($Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            return
        } catch {
            if ($attempt -eq 5) {
                throw @"
Failed to delete '$Path'.

The old build output is probably still in use. Close any running 'USART Helper.exe',
close Explorer windows opened inside the dist/build folder, or wait for antivirus
scanning to finish, then rerun:

  .\build_windows.ps1 -Clean

Original error: $($_.Exception.Message)
"@
            }

            Start-Sleep -Milliseconds (300 * $attempt)
        }
    }
}

Set-Location $ProjectRoot

if ($Clean) {
    Write-Step "Cleaning old build output"
    Stop-PackagedAppFromDist
    foreach ($path in @($BuildDir, $DistDir, (Join-Path $ProjectRoot "installer_output"))) {
        Remove-PathWithRetry $path
    }
}

if ($SkipVenv) {
    $PythonExe = "python"
} else {
    if (-not (Test-Path -LiteralPath $VenvDir)) {
        Write-Step "Creating build virtual environment"
        python -m venv $VenvDir
    }
    $PythonExe = Join-Path $VenvDir "Scripts\python.exe"
}

if ($NoInstall) {
    Write-Step "Skipping dependency installation"
} else {
    Write-Step "Installing build dependencies"
    & $PythonExe -m pip install --upgrade pip
    & $PythonExe -m pip install -r (Join-Path $ProjectRoot "requirements.txt") -r (Join-Path $ProjectRoot "requirements-build.txt")
}

Write-Step "Building standalone application"
& $PythonExe -m PyInstaller --noconfirm --clean $SpecPath

if (-not (Test-Path -LiteralPath $AppExe)) {
    throw "Build did not produce expected executable: $AppExe"
}

Write-Host ""
Write-Host "Standalone app ready:" -ForegroundColor Green
Write-Host "  $AppExe"

if ($Installer) {
    $Iscc = Resolve-Iscc
    if (-not $Iscc) {
        throw "Inno Setup compiler was not found. Install Inno Setup 6 on the build computer, then rerun: .\build_windows.ps1 -Installer"
    }

    Write-Step "Building Windows installer"
    $IssPath = Join-Path $ProjectRoot "installer\USART_Helper.iss"
    & $Iscc $IssPath

    $SetupExe = Join-Path $ProjectRoot "installer_output\USART_Helper_Setup.exe"
    if (-not (Test-Path -LiteralPath $SetupExe)) {
        throw "Installer build did not produce expected file: $SetupExe"
    }

    Write-Host ""
    Write-Host "Installer ready:" -ForegroundColor Green
    Write-Host "  $SetupExe"
}
