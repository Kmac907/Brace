. (Join-Path $PSScriptRoot 'TestSupport.ps1')
$sourceRoot = Split-Path $PSScriptRoot -Parent
$template = Join-Path $sourceRoot 'template'
$temporary = New-TestDirectory
$oldPath = $env:PATH
$oldProviderState = $env:RALPH_FAKE_GH_STATE
$oldAuditScope = $env:RALPH_FAKE_AUDIT_SCOPE_BLOCKER
try {
    $project = Join-Path $temporary 'project'
    $remote = Join-Path $temporary 'remote.git'
    [void][IO.Directory]::CreateDirectory($project)
    foreach ($entry in Get-ChildItem $template -Force) { Copy-Item $entry.FullName $project -Recurse -Force }
    $utf8 = [Text.UTF8Encoding]::new($false,$true)
    $configurationPath = Join-Path $project '.codex/workflow.json'
    $configuration = [IO.File]::ReadAllText($configurationPath,$utf8) | ConvertFrom-Json -Depth 50
    $configuration.github.repository = 'fixture/project'
    $configuration.worktreeRoot = Join-Path $temporary 'worktrees'
    [IO.File]::WriteAllText($configurationPath,($configuration|ConvertTo-Json -Depth 50),$utf8)
    [IO.File]::WriteAllText((Join-Path $project 'requirements.md'),"# Requirements`n`n- REQ-FUNC-001: Create a feature file.",$utf8)
    & git init --bare $remote | Out-Null
    & git init -b main $project | Out-Null
    & git -C $project config user.name 'Worktree Ralph Test'
    & git -C $project config user.email 'test@example.invalid'
    & git -C $project add --all
    & git -C $project commit -m 'Initial project' | Out-Null
    $hosted = 'https://github.com/fixture/project.git'
    & git -C $project config "url.$remote.insteadOf" $hosted
    & git -C $project remote add origin $hosted
    & git -C $project push --set-upstream origin main | Out-Null

    $env:PATH = (Join-Path $PSScriptRoot 'fixtures/fake-bin') + [IO.Path]::PathSeparator + $oldPath
    $env:RALPH_FAKE_GH_STATE = Join-Path $temporary 'pull-requests.json'
    Remove-Item Env:RALPH_FAKE_AUDIT_SCOPE_BLOCKER -ErrorAction SilentlyContinue
    & (Join-Path $project '.codex/scripts/planning-loop.ps1') -Repository $project
    & (Join-Path $project '.codex/scripts/build-loop.ps1') -Repository $project

    $env:RALPH_FAKE_AUDIT_SCOPE_BLOCKER = '1'
    & (Join-Path $project '.codex/scripts/audit-loop.ps1') -Repository $project -PmInputReader { param($Analysis) 'OPTION-0001' }

    $state = [IO.File]::ReadAllText((Join-Path $project '.codex/state.json'),$utf8) | ConvertFrom-Json -Depth 100
    $tasks = [IO.File]::ReadAllText((Join-Path $project '.codex/tasks.json'),$utf8) | ConvertFrom-Json -Depth 100
    $bugs = [IO.File]::ReadAllText((Join-Path $project '.codex/bugs.json'),$utf8) | ConvertFrom-Json -Depth 100
    Assert-TestEqual 'complete' ([string]$state.stage) 'audit scope expansion completes after returning through build'
    Assert-TestEqual 2 @($tasks.tasks).Count 'audit amendment appends one task'
    Assert-TestTrue (@($tasks.tasks | Where-Object status -eq 'integrated').Count -eq 2) 'original and follow-up tasks remain integrated'
    Assert-TestEqual 'AMEND-0001' ([string]$tasks.tasks[1].amendmentId) 'audit follow-up task records amendment identity'
    Assert-TestEqual 'complete' ([string]$bugs.status) 'fresh post-expansion audit completes'
    Assert-TestTrue ([IO.File]::Exists((Join-Path $project '.codex/results/AMEND-0001-pre-expansion-audit.json'))) 'pre-expansion audit ledger is preserved'
    Assert-TestTrue (([IO.File]::ReadAllText((Join-Path $project 'requirements.md'),$utf8)).Contains('REQ-FUNC-002')) 'final main contains audit-approved requirement'
    Assert-TestTrue ([IO.File]::Exists((Join-Path $project 'expanded.txt'))) 'follow-up implementation reached final main'
    Assert-TestTrue (@(Get-ChildItem (Join-Path $project '.codex/logs') -Filter 'auditor-*.result.json').Count -ge 2) 'audit reruns from a fresh whole-project result after expansion'
}
finally {
    $env:PATH = $oldPath
    $env:RALPH_FAKE_GH_STATE = $oldProviderState
    $env:RALPH_FAKE_AUDIT_SCOPE_BLOCKER = $oldAuditScope
    Remove-TestDirectory $temporary
}

Write-Host "Audit scope expansion tests passed: $script:RalphTestCount assertions"
