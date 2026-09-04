Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$tests = @(
    'Common.Tests.ps1',
    'GitWorktree.Tests.ps1',
    'Reconciliation.Tests.ps1',
    'Migration.Tests.ps1',
    'PmWorkflow.Tests.ps1',
    'PmValidation.Tests.ps1',
    'PmDecision.Tests.ps1',
    'AssignmentRecovery.Tests.ps1',
    'AuditScopeExpansion.Tests.ps1',
    'ProcessSafety.Tests.ps1',
    'Bootstrap.Tests.ps1',
    'EndToEnd.Tests.ps1'
)
foreach ($test in $tests) {
    Write-Host "RUNNING $test"
    & (Join-Path $PSScriptRoot $test)
}
Write-Host 'ALL WORKTREE RALPH TESTS PASSED'
