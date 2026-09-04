. (Join-Path $PSScriptRoot 'TestSupport.ps1')

$temporary = New-TestDirectory
$originalPath = $env:PATH
$originalState = $env:RALPH_FAKE_GH_STATE
$originalRemote = $env:RALPH_FAKE_BOOTSTRAP_REMOTE
try {
    $source = Join-Path $temporary 'source'
    $projects = Join-Path $temporary 'projects'
    $remote = Join-Path $temporary 'remote.git'
    [void][System.IO.Directory]::CreateDirectory($source)
    [void][System.IO.Directory]::CreateDirectory($projects)
    Copy-Item -LiteralPath (Join-Path (Split-Path $PSScriptRoot -Parent) 'template') -Destination $source -Recurse
    [void](& git -C $source init -b main 2>&1)
    [void](& git -C $source config user.name 'Worktree Ralph Test' 2>&1)
    [void](& git -C $source config user.email 'test@example.invalid' 2>&1)
    [void](& git -C $source add --all 2>&1)
    [void](& git -C $source commit -m template 2>&1)

    $env:PATH = (Join-Path $PSScriptRoot 'fixtures\fake-bin') + [IO.Path]::PathSeparator + $originalPath
    $env:RALPH_FAKE_GH_STATE = Join-Path $temporary 'pull-requests.json'
    $env:RALPH_FAKE_BOOTSTRAP_REMOTE = $remote
    & (Join-Path (Split-Path $PSScriptRoot -Parent) 'New-WorktreeRalphProject.ps1') `
        -SourceRepository $source `
        -ProjectName 'fixture-project' `
        -ParentDirectory $projects `
        -Provider github `
        -GitHubOwner fixture-owner `
        -GitUserName 'Worktree Ralph Test' `
        -GitUserEmail 'test@example.invalid'

    $target = Join-Path $projects 'fixture-project'
    Assert-TestTrue -Condition ([IO.File]::Exists((Join-Path $target 'requirements.md'))) -Message 'requirements template copied'
    Assert-TestTrue -Condition ([IO.File]::Exists((Join-Path $target 'REQUIREMENTS-PROMPT.md'))) -Message 'requirements authoring prompt copied'
    Assert-TestTrue -Condition ([IO.File]::Exists((Join-Path $target '.codex\scripts\planning-loop.ps1'))) -Message 'planning loop copied'
    Assert-TestTrue -Condition ([IO.File]::Exists((Join-Path $target '.codex\state.json'))) -Message 'workflow state initialized'
    $workflow = [IO.File]::ReadAllText((Join-Path $target '.codex\workflow.json')) | ConvertFrom-Json
    Assert-TestEqual -Expected 'fixture-owner/fixture-project' -Actual ([string]$workflow.github.repository) -Message 'provider identity configured'
    $local = (& git -C $target rev-parse HEAD).Trim()
    $published = (& git -C $target rev-parse origin/main).Trim()
    Assert-TestEqual -Expected $local -Actual $published -Message 'initial commit published'
    Assert-TestTrue -Condition (-not [IO.Directory]::Exists((Join-Path $target 'template'))) -Message 'only template contents copied'
} finally {
    $env:PATH = $originalPath
    $env:RALPH_FAKE_GH_STATE = $originalState
    $env:RALPH_FAKE_BOOTSTRAP_REMOTE = $originalRemote
    Remove-TestDirectory $temporary
}

Write-Host "Bootstrap tests passed: $script:RalphTestCount assertions"
