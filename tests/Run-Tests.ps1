Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$tests = @(
    'Common.Tests.ps1',
    'GitWorktree.Tests.ps1',
    'Reconciliation.Tests.ps1',
    'ProcessSafety.Tests.ps1',
    'Bootstrap.Tests.ps1',
    'EndToEnd.Tests.ps1'
)
foreach ($test in $tests) {
    Write-Host "RUNNING $test"
    & (Join-Path $PSScriptRoot $test)
}
Write-Host 'ALL WORKTREE RALPH TESTS PASSED'
