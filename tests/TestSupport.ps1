Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:RalphTestCount = 0

function Assert-TestTrue {
    param([Parameter(Mandatory)][bool]$Condition, [Parameter(Mandatory)][string]$Message)
    $script:RalphTestCount += 1
    if (-not $Condition) { throw "ASSERTION FAILED: $Message" }
}

function Assert-TestEqual {
    param($Expected, $Actual, [Parameter(Mandatory)][string]$Message)
    $script:RalphTestCount += 1
    if ($Expected -cne $Actual) { throw "ASSERTION FAILED: $Message. Expected '$Expected', actual '$Actual'." }
}

function Assert-TestThrows {
    param([Parameter(Mandatory)][scriptblock]$Action, [Parameter(Mandatory)][string]$Pattern)
    $script:RalphTestCount += 1
    try { & $Action; throw 'Expected action to throw.' } catch {
        if ($_.Exception.Message -notmatch $Pattern) { throw "ASSERTION FAILED: exception '$($_.Exception.Message)' did not match '$Pattern'." }
    }
}

function New-TestDirectory {
    $path = Join-Path ([System.IO.Path]::GetTempPath()) ("worktree-ralph-test-{0}" -f [Guid]::NewGuid().ToString('N'))
    [void][System.IO.Directory]::CreateDirectory($path)
    $path
}

function Remove-TestDirectory {
    param([Parameter(Mandatory)][string]$Path)
    $temp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    $target = [System.IO.Path]::GetFullPath($Path)
    $relative = [System.IO.Path]::GetRelativePath($temp, $target)
    if ($relative -notmatch '^worktree-ralph-test-[0-9a-f]{32}([\\/]|$)') { throw "Unsafe test cleanup path: $target" }
    if ([System.IO.Directory]::Exists($target)) { Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop }
}
