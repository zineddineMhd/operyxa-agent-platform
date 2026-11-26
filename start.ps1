# Démarre toute la plateforme avec Docker Desktop, sous Windows.
#   Démarrer : clic droit sur start.ps1 > "Exécuter avec PowerShell"
#              ou, dans PowerShell :  .\start.ps1
#   Arrêter  : .\start.ps1 stop        Journaux : .\start.ps1 logs
param([ValidateSet("start", "stop", "logs")][string]$Action = "start")

# Continue: Windows PowerShell 5.1 turns docker progress on stderr into errors under "Stop".
$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot
$compose = @("compose", "-f", "infrastructure/compose.yml")

function Stop-WithMessage([string]$Message) {
    Write-Host $Message -ForegroundColor Red
    if ($Host.Name -eq "ConsoleHost") { Read-Host "Appuyez sur Entrée pour fermer" | Out-Null }
    exit 1
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Stop-WithMessage "Docker est introuvable. Installez Docker Desktop : https://www.docker.com/products/docker-desktop/"
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Stop-WithMessage "Docker Desktop ne tourne pas. Lancez-le, attendez 'Engine running', puis relancez ce script."
}

if ($Action -eq "stop") {
    docker @compose stop
    Write-Host "Plateforme arrêtée. Vos données sont conservées." -ForegroundColor Green
    exit 0
}
if ($Action -eq "logs") {
    docker @compose logs -f --tail 100
    exit 0
}

function New-Secret {
    $bytes = New-Object byte[] 48
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return ([Convert]::ToBase64String($bytes) -replace "[/+=]", "")
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Fichier .env créé."
}
$lines = Get-Content ".env" -Encoding UTF8
$changed = $false
$lines = $lines | ForEach-Object {
    if ($_ -match "^JWT_SECRET_KEY=(replace_me.*)?$") { $changed = $true; "JWT_SECRET_KEY=$(New-Secret)" }
    elseif ($_ -match "^SECRETS_KEY=$") { $changed = $true; "SECRETS_KEY=$(New-Secret)" }
    else { $_ }
}
if ($changed) {
    # UTF-8 without BOM, so that Docker reads the first line correctly.
    [System.IO.File]::WriteAllLines((Join-Path $PSScriptRoot ".env"), [string[]]$lines, (New-Object System.Text.UTF8Encoding $false))
    Write-Host "Clés secrètes générées dans .env."
}
if ($lines -match "^LLM_PROVIDER=demo") {
    Write-Host "Note : LLM_PROVIDER=demo. Pour une vraie analyse, mettez votre clé dans .env (voir le guide)." -ForegroundColor Yellow
}

Write-Host "Construction et démarrage (la première fois : plusieurs minutes)..."
docker @compose up -d --build
if ($LASTEXITCODE -ne 0) { Stop-WithMessage "Le démarrage a échoué. Lisez les messages ci-dessus." }

for ($i = 0; $i -lt 90; $i++) {
    try {
        Invoke-WebRequest -Uri "http://localhost:8080/healthz" -UseBasicParsing -TimeoutSec 3 | Out-Null
        Write-Host ""
        Write-Host "Operyxa est prêt : http://localhost:8080" -ForegroundColor Green
        Write-Host "Documentation de l'API : http://localhost:8000/docs"
        Write-Host "Arrêt : .\start.ps1 stop    Journaux : .\start.ps1 logs"
        Start-Process "http://localhost:8080"
        if ($Host.Name -eq "ConsoleHost") { Read-Host "Appuyez sur Entrée pour fermer cette fenêtre" | Out-Null }
        exit 0
    } catch { Start-Sleep -Seconds 2 }
}
Stop-WithMessage "La plateforme ne répond pas encore. Regardez les journaux : .\start.ps1 logs"
