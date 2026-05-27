param(
    [switch]$NoPathUpdate
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BinDir = Join-Path $env:USERPROFILE ".local\bin"
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

function New-DarkCodexWrapper {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $WrapperPath = Join-Path $BinDir "$Name.cmd"
    $Content = @"
@echo off
cd /d "$Root"
python -m darkcodex %*
"@
    Set-Content -LiteralPath $WrapperPath -Value $Content -Encoding ASCII
}

New-DarkCodexWrapper -Name "darkcodex"
New-DarkCodexWrapper -Name "DarkCodex"

if (-not $NoPathUpdate) {
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $PathParts = @()
    if ($UserPath) {
        $PathParts = $UserPath -split ";" | Where-Object { $_ }
    }
    if ($PathParts -notcontains $BinDir) {
        $NewPath = (($PathParts + $BinDir) -join ";")
        [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
        $env:Path = "$env:Path;$BinDir"
    }
}

Write-Host "--------------------------------------------------"
Write-Host "DarkCodex installe avec succes dans $BinDir"
Write-Host "Commandes disponibles: darkcodex, DarkCodex"
Write-Host "--------------------------------------------------"
Write-Host "Pour commencer dans ce terminal: darkcodex doctor"
Write-Host "Si la commande n'est pas trouvee, ferme puis relance le terminal."
Write-Host "Pour enregistrer Gemini: darkcodex config api-key VOTRE_CLE"
Write-Host "--------------------------------------------------"
