param()

$ErrorActionPreference = 'Stop'
$demoRepoRoot = Split-Path -Parent $PSScriptRoot
$demoStateDirectory = Join-Path $demoRepoRoot ('.tmp/oracle-demo/' + [guid]::NewGuid().ToString())

function Invoke-OracleDemoStep {
    param([string]$Label, [string[]]$CliArguments, [string]$ExpectedStatus)

    $demoOutput = & uv run --offline --no-sync terac-oracle --state-dir $demoStateDirectory @CliArguments
    if ($LASTEXITCODE -ne 0) { throw "Oracle CLI failed during $Label." }
    $snapshot = ($demoOutput -join [Environment]::NewLine) | ConvertFrom-Json
    if ($snapshot.status -ne $ExpectedStatus) { throw "Unexpected state during $Label." }
    Write-Host ("{0}: case={1}; status={2}; phase={3}" -f $Label, $snapshot.replay_case, $snapshot.status, $snapshot.phase)
    if ($snapshot.question) { Write-Host ('  Question: ' + $snapshot.question) }
    if ($snapshot.feedback) {
        Write-Host ('  Feedback: ' + $snapshot.feedback.recommendation + ' / ' + $snapshot.feedback.provenance)
        Write-Host ('  Rationale: ' + $snapshot.feedback.rationale)
    }
    return $snapshot
}

Push-Location -LiteralPath $demoRepoRoot
try {
    Write-Host 'Oracle meeting demo - fictional input, scripted synthetic expert, offline replay'
    Write-Host ('Local checkpoints: ' + $demoStateDirectory)
    $started = Invoke-OracleDemoStep '1 start' @('start', '--brief', 'Synthetic repair-shop appointment tool: buyer demand and pricing are unproven.') 'working'
    $null = Invoke-OracleDemoStep '2 inspect waiting (pure read)' @('inspect', $started.run_id) 'working'
    $null = Invoke-OracleDemoStep '3 resume one event' @('resume', $started.run_id) 'needs_input'
    $null = Invoke-OracleDemoStep '4 inspect clarification (pure read)' @('inspect', $started.run_id) 'needs_input'
    $null = Invoke-OracleDemoStep '5 respond without progressing' @('respond', $started.run_id, '--answer', 'Independent repair shops') 'working'
    $delivered = Invoke-OracleDemoStep '6 resume to feedback' @('resume', $started.run_id) 'succeeded'
    if ($delivered.feedback.recommendation -ne 'inconclusive') { throw 'Expected scripted inconclusive review.' }
    $null = Invoke-OracleDemoStep '7 inspect delivered feedback (pure read)' @('inspect', $started.run_id) 'succeeded'
    $linked = Invoke-OracleDemoStep '8 linked follow-up' @('start', '--brief', 'Synthetic follow-up: investigate the channel hypothesis.', '--parent-id', $started.run_id, '--intent', 'follow_up', '--replay-case', 'pass_with_comments') 'working'
    if ($linked.parent_id -ne $started.run_id -or $linked.feedback) { throw 'Linked work must retain lineage and begin with no verdict.' }
    $linkedDelivered = Invoke-OracleDemoStep '9 deliver linked pass with comments' @('resume', $linked.run_id) 'succeeded'
    if ($linkedDelivered.feedback.recommendation -ne 'pass' -or -not $linkedDelivered.feedback.comments) { throw 'Expected a passing linked review with advisory comments.' }
    $failedCase = Invoke-OracleDemoStep '10 start material-fail case' @('start', '--brief', 'Synthetic repair-shop GTM brief: 20 customers are paying pilot customers. Evidence: 20 free trials and zero paid commitments.', '--replay-case', 'fail') 'working'
    $failedReview = Invoke-OracleDemoStep '11 deliver material-fail case' @('resume', $failedCase.run_id) 'succeeded'
    if ($failedReview.feedback.recommendation -ne 'fail' -or -not $failedReview.feedback.findings) { throw 'Expected a material finding in the fail case.' }
    Write-Host 'Done. The calling agent can present, revise, continue or request more scoped work.'
} finally {
    Pop-Location
}
