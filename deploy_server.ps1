param(
    [Parameter(Mandatory = $true)]
    [string]$RepoUrl,

    [string]$Branch = "main",
    [string]$TargetDir = "C:\FinanceBot"
)

$scriptPath = Join-Path $PSScriptRoot "scripts\deploy_server.ps1"
& $scriptPath -RepoUrl $RepoUrl -Branch $Branch -TargetDir $TargetDir
