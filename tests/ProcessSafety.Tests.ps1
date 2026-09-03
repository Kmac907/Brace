. (Join-Path $PSScriptRoot 'TestSupport.ps1')
$sourceRoot=Split-Path $PSScriptRoot -Parent
. (Join-Path $sourceRoot 'template\.codex\scripts\common.ps1')
$temporary=New-TestDirectory;$oldPath=$env:PATH;$oldPid=$env:RALPH_HANG_CHILD_PID
try {
    $env:PATH=(Join-Path $sourceRoot 'tests\fixtures\hanging-bin')+[IO.Path]::PathSeparator+$oldPath
    $env:RALPH_HANG_CHILD_PID=Join-Path $temporary 'child.pid'
    $schema=Join-Path $sourceRoot 'template\.codex\schemas\verifier-result.schema.json'
    Assert-TestThrows { Invoke-RalphCodex 'hang' $temporary $schema 'read-only' $temporary 'timeout-test' 90 2 2 } 'deadline'
    $deadline=[DateTime]::UtcNow.AddSeconds(3);while(-not[IO.File]::Exists($env:RALPH_HANG_CHILD_PID)-and[DateTime]::UtcNow-lt$deadline){Start-Sleep -Milliseconds 50}
    Assert-TestTrue ([IO.File]::Exists($env:RALPH_HANG_CHILD_PID)) 'hanging fixture created a descendant process'
    $childPid=[int](Read-RalphText $env:RALPH_HANG_CHILD_PID);Start-Sleep -Milliseconds 200
    Assert-TestTrue ($null-eq(Get-Process -Id $childPid -ErrorAction SilentlyContinue)) 'timed-out Codex descendant was terminated'
}
finally{$env:PATH=$oldPath;$env:RALPH_HANG_CHILD_PID=$oldPid;Remove-TestDirectory $temporary}
Write-Host "Process safety tests passed: $script:RalphTestCount assertions"
