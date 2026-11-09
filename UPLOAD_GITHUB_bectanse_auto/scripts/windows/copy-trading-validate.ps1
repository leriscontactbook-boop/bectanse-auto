param(
  [string]$InstallRoot = 'C:\Bectanse\CopyTrading',
  [Parameter(Mandatory=$true)][string]$ReleaseRoot,
  [Parameter(Mandatory=$true)][string]$MasterTerminalPath,
  [Parameter(Mandatory=$true)][string]$FollowerTerminalPath,
  [Parameter(Mandatory=$true)][string]$ExpectedMasterLogin,
  [Parameter(Mandatory=$true)][string]$ExpectedFollowerLogin,
  [Parameter(Mandatory=$true)][string]$ExpectedMasterMemberCode,
  [Parameter(Mandatory=$true)][string]$ExpectedFollowerMemberCode,
  [string]$AdminSessionCookieName = 'session'
)
$ErrorActionPreference = 'Stop'

foreach ($login in @($ExpectedMasterLogin, $ExpectedFollowerLogin)) {
  if ($login -notmatch '^\d+$') { throw 'Les logins MT5 attendus doivent être numériques.' }
}
if ($AdminSessionCookieName -notmatch '^[A-Za-z0-9_-]+$') { throw 'Nom de cookie invalide.' }
if (-not $ExpectedMasterMemberCode.Trim() -or -not $ExpectedFollowerMemberCode.Trim()) {
  throw 'Les codes membres maître et suiveur sont requis pour corréler les preuves du backend.'
}
$masterPath = (Resolve-Path -LiteralPath $MasterTerminalPath).Path
$followerPath = (Resolve-Path -LiteralPath $FollowerTerminalPath).Path
if ($masterPath -ieq $followerPath) { throw 'Le terminal maître et le terminal suiveur doivent être distincts.' }
$releaseRoot = (Resolve-Path -LiteralPath $ReleaseRoot).Path
$installRoot = [IO.Path]::GetFullPath($InstallRoot)
$logRoot = Join-Path $installRoot 'logs'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$runId = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
$reportPath = Join-Path $logRoot "validation-$runId.json"
$releasesRoot = [IO.Path]::GetFullPath((Join-Path $installRoot 'releases')).TrimEnd('\') + '\'
if (-not $releaseRoot.StartsWith($releasesRoot, [StringComparison]::OrdinalIgnoreCase)) {
  throw 'ReleaseRoot doit désigner une release installée sous le dossier releases.'
}

# Health covers the installed task, executable, interactive Windows session,
# worker heartbeat, release identity, Railway /health and PostgreSQL readiness.
Write-Host '== Windows worker / Railway / PostgreSQL / logs =='
& (Join-Path $releaseRoot 'scripts\windows\copy-trading-health.ps1') -InstallRoot $installRoot
if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'Le contrôle de santé worker/backend a échoué.' }

$probeResults = @{}
function Quote-ProcessArgument([string]$value) {
  return '"' + $value.Replace('"', '\"') + '"'
}
foreach ($spec in @(
  @{Role='MASTER'; Path=$masterPath; Login=$ExpectedMasterLogin},
  @{Role='FOLLOWER'; Path=$followerPath; Login=$ExpectedFollowerLogin}
)) {
  Write-Host "== Terminal $($spec.Role) : connexion native et compte démo =="
  $resultPath = Join-Path $logRoot "terminal-$($spec.Role.ToLowerInvariant())-$runId.json"
  $probeScript = Join-Path $releaseRoot 'scripts\windows\test-terminal-interactive.ps1'
  $childArgs = @(
    '-NoProfile', '-ExecutionPolicy', 'RemoteSigned', '-File', $probeScript,
    '-InstallRoot', $installRoot, '-ReleaseRoot', $releaseRoot,
    '-TerminalPath', $spec.Path, '-ExpectedLogin', $spec.Login,
    '-ResultPath', $resultPath, '-PromptDemoCredentials'
  )
  $quotedArgs = ($childArgs | ForEach-Object { Quote-ProcessArgument ([string]$_) }) -join ' '
  $child = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') `
    -ArgumentList $quotedArgs -Wait -PassThru -NoNewWindow
  if ($child.ExitCode -ne 0) {
    throw "La vérification MT5 native du terminal $($spec.Role) a échoué. Aucun ordre n'a été envoyé."
  }
  $probe = Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8 | ConvertFrom-Json
  if (-not $probe.connected -or -not $probe.terminal_path_verified -or
      -not $probe.demo_account_verified -or -not $probe.expected_login_matches) {
    throw "La preuve du terminal $($spec.Role) ne correspond pas au compte démo et au chemin attendus."
  }
  $probeResults[$spec.Role] = $probe
}

$configPath = Join-Path $installRoot 'config\.worker.env'
$backendLine = Get-Content -LiteralPath $configPath -Encoding UTF8 |
  Where-Object { $_ -match '^COPY_TRADING_BACKEND_URL=' } | Select-Object -First 1
if (-not $backendLine) { throw 'URL backend absente de la configuration worker.' }
$backendUrl = ($backendLine -replace '^COPY_TRADING_BACKEND_URL=', '').TrimEnd('/')
Write-Host 'Lecture admin des événements, jobs, positions, clôtures et reprises (sans écriture).'
$secureCookie = Read-Host "Session administrateur ($AdminSessionCookieName) pour le rapport" -AsSecureString
$cookiePtr = [IntPtr]::Zero
try {
  $cookiePtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureCookie)
  $cookieValue = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($cookiePtr)
  $operations = Invoke-RestMethod -Uri "$backendUrl/admin/api/copy-trading/operations" `
    -Method Get -Headers @{ Cookie = "$AdminSessionCookieName=$cookieValue" } -TimeoutSec 20
} finally {
  if ($cookiePtr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($cookiePtr) }
  $cookieValue = $null
  $secureCookie = $null
}
if (-not $operations.ok) { throw 'Le rapport admin des opérations n’a pas pu être validé.' }

$pairProofs = @($operations.demo_native_confirmations_by_pair_24h | Where-Object {
  ([string]$_.master_member_code).Equals($ExpectedMasterMemberCode.Trim(), [StringComparison]::OrdinalIgnoreCase) -and
  ([string]$_.follower_member_code).Equals($ExpectedFollowerMemberCode.Trim(), [StringComparison]::OrdinalIgnoreCase)
})
$openProofs = 0
$closeProofs = 0
foreach ($proof in $pairProofs) {
  if ($proof.action -eq 'open') { $openProofs += [int]$proof.count }
  if ($proof.action -eq 'close') { $closeProofs += [int]$proof.count }
}
$unresolved = [int]$operations.recovery_required_jobs_24h
$blocked = if ($operations.blocked_followers) { @($operations.blocked_followers).Count } else { 0 }
$demoMode = ([string]$operations.execution_mode -eq 'demo') -and ($operations.execution_enabled -eq $true)
$workflowReady = $demoMode -and $openProofs -gt 0 -and $closeProofs -gt 0 -and
  $unresolved -eq 0 -and $blocked -eq 0
$report = [ordered]@{
  checked_at_utc = [DateTime]::UtcNow.ToString('o')
  execution_mode = $operations.execution_mode
  execution_enabled = $operations.execution_enabled
  master_terminal = $probeResults.MASTER
  follower_terminal = $probeResults.FOLLOWER
  expected_master_member_code = $ExpectedMasterMemberCode.Trim()
  expected_follower_member_code = $ExpectedFollowerMemberCode.Trim()
  events_24h = $operations.events_24h
  jobs_24h = $operations.jobs_24h
  job_actions_24h = $operations.job_actions_24h
  native_demo_open_confirmations_24h_for_pair = $openProofs
  native_demo_close_confirmations_24h_for_pair = $closeProofs
  position_links_by_state = $operations.position_links_by_state
  recovery_required_jobs_24h = $unresolved
  blocked_followers = $blocked
  terminal_probes = @($operations.terminal_probes | Select-Object role,member_code,state,failure_code,
    diagnostic_code,terminal_path_verified,process_id,session_id,checked_at)
  recent_audit = $operations.recent
  orders_sent_by_validation = $false
  report = if ($workflowReady) {
    'READ_ONLY_CHECKS_PASSED_WITH_RECENT_DEMO_OPEN_AND_CLOSE_EVIDENCE'
  } else { 'DEMO_END_TO_END_OR_RECOVERY_NOT_VERIFIED' }
}
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding UTF8
Write-Host "Rapport: $reportPath"
Write-Host "Événements 24 h: $($operations.events_24h | ConvertTo-Json -Compress)"
Write-Host "Jobs 24 h: $($operations.jobs_24h | ConvertTo-Json -Compress)"
Write-Host "Preuves démo ouverture/clôture: $openProofs / $closeProofs"
Write-Host "Mode démo activé: $demoMode"
Write-Host "Jobs à vérifier: $unresolved; comptes bloqués: $blocked"
Write-Host 'Aucun ordre de test ou changement de position n’a été envoyé par ce contrôle.'
if (-not $workflowReady) { exit 2 }
Write-Host 'Contrôles read-only réussis avec preuves démo récentes.'
