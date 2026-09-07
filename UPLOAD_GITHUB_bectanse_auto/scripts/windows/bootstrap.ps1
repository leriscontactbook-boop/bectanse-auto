param([string]$InstallRoot = "C:\Bectanse\MT5Worker")
$ErrorActionPreference = "Stop"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    winget install --id Python.Python.3.12 --exact --silent --accept-package-agreements --accept-source-agreements
  } else {
    $pythonVersion = "3.12.10"
    $pythonInstaller = Join-Path $env:TEMP "python-$pythonVersion-amd64.exe"
    try {
      Invoke-WebRequest "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-amd64.exe" -OutFile $pythonInstaller
      $signature = Get-AuthenticodeSignature $pythonInstaller
      if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notlike "*Python Software Foundation*") {
        throw "La signature de l'installateur Python n'est pas valide."
      }
      $process = Start-Process $pythonInstaller -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0 Include_launcher=1" -Wait -PassThru
      if ($process.ExitCode -ne 0) { throw "Installation Python échouée (code $($process.ExitCode))." }
    } finally {
      Remove-Item $pythonInstaller -Force -ErrorAction SilentlyContinue
    }
  }
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python 3.12 n'est pas disponible après installation." }
& "$PSScriptRoot\install.ps1" -InstallRoot $InstallRoot
