[CmdletBinding(PositionalBinding = $false)]
param([Parameter(ValueFromRemainingArguments)][string[]]$CliArguments)

$ErrorActionPreference = 'Stop'
$statePath = $env:RALPH_FAKE_GH_STATE
if ([string]::IsNullOrWhiteSpace($statePath)) { throw 'RALPH_FAKE_GH_STATE is required.' }
$utf8 = [System.Text.UTF8Encoding]::new($false, $true)

function Get-ValueAfter([string]$Name) {
    $index = [Array]::IndexOf($CliArguments, $Name)
    if ($index -lt 0 -or $index + 1 -ge $CliArguments.Count) { return $null }
    $CliArguments[$index + 1]
}
function Read-PullRequests {
    if (-not [System.IO.File]::Exists($statePath)) { return @() }
    @([System.IO.File]::ReadAllText($statePath, $utf8) | ConvertFrom-Json -Depth 30)
}
function Save-PullRequests([object[]]$PullRequests) {
    [System.IO.File]::WriteAllText($statePath, ($PullRequests | ConvertTo-Json -Depth 30 -AsArray), $utf8)
}

if ($CliArguments[0] -ceq 'auth' -and $CliArguments[1] -ceq 'status') { exit 0 }
if ($CliArguments[0] -ceq 'api' -and $CliArguments[1] -ceq 'user') {
    Write-Output 'fixture-owner'
    exit 0
}
if ($CliArguments[0] -ceq 'repo' -and $CliArguments[1] -ceq 'view') {
    exit 1
}
if ($CliArguments[0] -ceq 'repo' -and $CliArguments[1] -ceq 'create') {
    if ([string]::IsNullOrWhiteSpace($env:RALPH_FAKE_BOOTSTRAP_REMOTE)) {
        throw 'RALPH_FAKE_BOOTSTRAP_REMOTE is required for repo create.'
    }
    $source = Get-ValueAfter '--source'
    [void](& git init --bare $env:RALPH_FAKE_BOOTSTRAP_REMOTE 2>&1)
    if ($LASTEXITCODE -ne 0) { throw 'Unable to initialize fake bootstrap remote.' }
    [void](& git -C $source remote add origin $env:RALPH_FAKE_BOOTSTRAP_REMOTE 2>&1)
    if ($LASTEXITCODE -ne 0) { throw 'Unable to configure fake bootstrap remote.' }
    [void](& git -C $source push --set-upstream origin main 2>&1)
    if ($LASTEXITCODE -ne 0) { throw 'Unable to push fake bootstrap remote.' }
    exit 0
}
if ($CliArguments[0] -ceq 'pr' -and $CliArguments[1] -ceq 'list') {
    $head = Get-ValueAfter '--head'
    $base = Get-ValueAfter '--base'
    $pullRequestMatches = @(Read-PullRequests | Where-Object { $_.headRefName -ceq $head -and $_.baseRefName -ceq $base })
    Write-Output (ConvertTo-Json -InputObject $pullRequestMatches -Depth 30 -Compress)
    exit 0
}
if ($CliArguments[0] -ceq 'pr' -and $CliArguments[1] -ceq 'create') {
    $items = @(Read-PullRequests)
    $head = Get-ValueAfter '--head'
    $base = Get-ValueAfter '--base'
    $number = $items.Count + 1
    $items += [pscustomobject]@{number=$number;url="https://example.invalid/pr/$number";state='OPEN';headRefName=$head;baseRefName=$base;mergeCommit=$null}
    Save-PullRequests $items
    Write-Output "https://example.invalid/pr/$number"
    exit 0
}
if ($CliArguments[0] -ceq 'pr' -and $CliArguments[1] -ceq 'merge') {
    $id = [int]$CliArguments[2]
    $items = @(Read-PullRequests)
    $item = @($items | Where-Object number -eq $id)[0]
    & git fetch origin $item.headRefName
    if ($LASTEXITCODE -ne 0) { throw 'Fake provider could not fetch PR head.' }
    $headSha = (& git rev-parse "origin/$($item.headRefName)").Trim()
    & git push origin "refs/remotes/origin/$($item.headRefName):refs/heads/$($item.baseRefName)"
    if ($LASTEXITCODE -ne 0) { throw 'Fake provider could not update PR base.' }
    $item.state = 'MERGED'
    $item.mergeCommit = [pscustomobject]@{oid=$headSha}
    if ('--delete-branch' -in $CliArguments) {
        & git push origin --delete $item.headRefName
        if ($LASTEXITCODE -ne 0) { throw 'Fake provider could not delete PR head.' }
    }
    Save-PullRequests $items
    exit 0
}
throw "Unsupported fake gh invocation: $($CliArguments -join ' ')"
