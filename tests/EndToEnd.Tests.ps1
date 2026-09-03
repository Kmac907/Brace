. (Join-Path $PSScriptRoot 'TestSupport.ps1')
$sourceRoot = Split-Path $PSScriptRoot -Parent
$template = Join-Path $sourceRoot 'template'
$temporary = New-TestDirectory
$oldPath = $env:PATH
$oldFakeState = $env:RALPH_FAKE_GH_STATE

try {
    $project = Join-Path $temporary 'project'
    $remote = Join-Path $temporary 'remote.git'
    [void][System.IO.Directory]::CreateDirectory($project)
    foreach ($entry in Get-ChildItem -LiteralPath $template -Force) { Copy-Item -LiteralPath $entry.FullName -Destination $project -Recurse -Force }
    $utf8 = [System.Text.UTF8Encoding]::new($false, $true)
    $configurationPath = Join-Path $project '.codex\workflow.json'
    $configuration = [System.IO.File]::ReadAllText($configurationPath, $utf8) | ConvertFrom-Json -Depth 50
    $configuration.github.repository = 'fixture/project'
    $configuration.maximumConcurrentBuilders = 2
    $configuration.maximumConcurrentFixers = 2
    $configuration.worktreeRoot = Join-Path $temporary 'worktrees'
    [System.IO.File]::WriteAllText($configurationPath, ($configuration | ConvertTo-Json -Depth 50), $utf8)
    [System.IO.File]::WriteAllText((Join-Path $project 'requirements.md'), "# Requirements`n`n- REQ-FUNC-001: Create a feature file.", $utf8)

    & git init --bare $remote | Out-Null
    & git init -b main $project | Out-Null
    & git -C $project config user.name 'Worktree Ralph Test'
    & git -C $project config user.email 'test@example.invalid'
    & git -C $project add --all
    & git -C $project commit -m 'Initial project' | Out-Null
    & git -C $project remote add origin $remote
    & git -C $project push --set-upstream origin main | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Unable to initialize end-to-end fixture.' }

    $env:PATH = (Join-Path $PSScriptRoot 'fixtures\fake-bin') + [System.IO.Path]::PathSeparator + $oldPath
    $env:RALPH_FAKE_GH_STATE = Join-Path $temporary 'pull-requests.json'

    & (Join-Path $project '.codex\scripts\planning-loop.ps1') -Repository $project
    & (Join-Path $project '.codex\scripts\build-loop.ps1') -Repository $project
    & (Join-Path $project '.codex\scripts\audit-loop.ps1') -Repository $project

    $state = [System.IO.File]::ReadAllText((Join-Path $project '.codex\state.json'), $utf8) | ConvertFrom-Json -Depth 50
    $tasks = [System.IO.File]::ReadAllText((Join-Path $project '.codex\tasks.json'), $utf8) | ConvertFrom-Json -Depth 50
    $bugs = [System.IO.File]::ReadAllText((Join-Path $project '.codex\bugs.json'), $utf8) | ConvertFrom-Json -Depth 50
    Assert-TestEqual -Expected 'complete' -Actual ([string]$state.stage) -Message 'end-to-end stage completed'
    Assert-TestEqual -Expected 'complete' -Actual ([string]$tasks.status) -Message 'task ledger completed'
    Assert-TestEqual -Expected 'integrated' -Actual ([string]$tasks.tasks[0].status) -Message 'task integrated'
    Assert-TestEqual -Expected 'complete' -Actual ([string]$bugs.status) -Message 'bug ledger completed'
    Assert-TestEqual -Expected 'verified' -Actual ([string]$bugs.bugs[0].status) -Message 'bug verified'
    $feature = [System.IO.File]::ReadAllText((Join-Path $project 'feature.txt'), $utf8)
    Assert-TestTrue -Condition ($feature -match 'implemented' -and $feature -match 'verified') -Message 'final main contains implementation and bug fix'
    $remainingBranches = @(& git -C $project branch --format='%(refname:short)' | Where-Object { $_ -ne 'main' })
    Assert-TestEqual -Expected 0 -Actual $remainingBranches.Count -Message 'owned local branches cleaned'
    Assert-TestTrue -Condition (-not [System.IO.Directory]::Exists((Join-Path $temporary 'worktrees'))) -Message 'external worktree directory cleaned'
} finally {
    $env:PATH = $oldPath
    $env:RALPH_FAKE_GH_STATE = $oldFakeState
    Remove-TestDirectory $temporary
}

Write-Host "End-to-end tests passed: $script:RalphTestCount assertions"
