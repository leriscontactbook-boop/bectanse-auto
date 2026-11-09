param(
  [switch]$ConfirmedNoActiveCopyJobs,
  [ValidateRange(1, 300)][int]$WaitSeconds = 30
)
$ErrorActionPreference = 'Stop'
$taskName = 'BectanseCopyTradingWorker'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $task) { Write-Host 'Tâche Copy Trading non installée.'; exit 0 }
if ($task.State -ne 'Running') { Write-Host "Tâche déjà arrêtée: $($task.State)"; exit 0 }
if (-not $ConfirmedNoActiveCopyJobs) {
  throw 'Avant arrêt, vérifier dans l’API admin qu’aucun job n’est actif et qu’aucun handoff n’est en cours; confirmer avec -ConfirmedNoActiveCopyJobs.'
}
Stop-ScheduledTask -TaskName $taskName
$deadline = (Get-Date).AddSeconds($WaitSeconds)
do {
  Start-Sleep -Seconds 1
  $task = Get-ScheduledTask -TaskName $taskName
} while ($task.State -eq 'Running' -and (Get-Date) -lt $deadline)
if ($task.State -eq 'Running') { throw 'La tâche ne s’est pas arrêtée dans le délai; aucun processus n’a été forcé.' }
$workers = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
  $_.CommandLine -match '-m\s+copy_trading'
})
if ($workers.Count -gt 0) { throw 'Processus Copy Trading résiduel détecté; aucun processus n’a été forcé.' }
Write-Host 'Tâche arrêtée sans tuer de processus; vérifier le statut backend avant toute reprise.'
