. (Join-Path $PSScriptRoot 'TestSupport.ps1')
$sourceRoot = Split-Path $PSScriptRoot -Parent

function New-PmDecisionFixture {
    $temporary = New-TestDirectory
    $project = Join-Path $temporary 'project'
    $remote = Join-Path $temporary 'remote.git'
    [void][IO.Directory]::CreateDirectory($project)
    foreach ($entry in Get-ChildItem (Join-Path $sourceRoot 'template') -Force) { Copy-Item $entry.FullName $project -Recurse -Force }
    $utf8 = [Text.UTF8Encoding]::new($false,$true)
    $configurationPath = Join-Path $project '.codex/workflow.json'
    $configuration = [IO.File]::ReadAllText($configurationPath,$utf8) | ConvertFrom-Json -Depth 50
    $configuration.github.repository = 'fixture/project'
    $configuration.maximumTaskAttempts = 2
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
    [pscustomobject]@{Temporary=$temporary;Project=$project;ProviderState=(Join-Path $temporary 'pull-requests.json');Utf8=$utf8}
}

$oldPath = $env:PATH
$oldProviderState = $env:RALPH_FAKE_GH_STATE
$oldSemantic = $env:RALPH_FAKE_SEMANTIC_BLOCKER
$oldOperational = $env:RALPH_FAKE_OPERATIONAL_BLOCKER
$oldDisposition = $env:RALPH_FAKE_BUG_DISPOSITION
try {
    $env:PATH = (Join-Path $PSScriptRoot 'fixtures/fake-bin') + [IO.Path]::PathSeparator + $oldPath

    $fixture = New-PmDecisionFixture
    try {
        $env:RALPH_FAKE_GH_STATE = $fixture.ProviderState
        $env:RALPH_FAKE_SEMANTIC_BLOCKER = '1'
        Remove-Item Env:RALPH_FAKE_OPERATIONAL_BLOCKER -ErrorAction SilentlyContinue
        & (Join-Path $fixture.Project '.codex/scripts/planning-loop.ps1') -Repository $fixture.Project
        Assert-TestThrows { & (Join-Path $fixture.Project '.codex/scripts/build-loop.ps1') -Repository $fixture.Project -PmInputReader { param($Analysis) 'OPTION-0002' } } 'User selected stop'
        $state = [IO.File]::ReadAllText((Join-Path $fixture.Project '.codex/state.json'),$fixture.Utf8) | ConvertFrom-Json -Depth 100
        $tasks = [IO.File]::ReadAllText((Join-Path $fixture.Project '.codex/tasks.json'),$fixture.Utf8) | ConvertFrom-Json -Depth 100
        Assert-TestEqual 'blocked' ([string]$state.stageStatus) 'rejected semantic amendment leaves workflow blocked'
        Assert-TestEqual 'awaiting_user' ([string]$state.activeAmendment.status) 'rejected decision remains durable'
        Assert-TestEqual 'OPTION-0002' ([string]$state.activeAmendment.selectedOptionId) 'exact rejected option is retained'
        Assert-TestEqual 1 ([int]$state.amendmentSequence) 'semantic blocker invoked one PM round'
        Assert-TestEqual 'result_ready' ([string]$tasks.tasks[0].status) 'rejected amendment preserves the blocked task result'
        Assert-TestTrue (-not [IO.File]::Exists($fixture.ProviderState)) 'rejected amendment creates no provider pull request'
    } finally { Remove-TestDirectory $fixture.Temporary }

    $fixture = New-PmDecisionFixture
    try {
        $env:RALPH_FAKE_GH_STATE = $fixture.ProviderState
        Remove-Item Env:RALPH_FAKE_SEMANTIC_BLOCKER -ErrorAction SilentlyContinue
        $env:RALPH_FAKE_OPERATIONAL_BLOCKER = '1'
        & (Join-Path $fixture.Project '.codex/scripts/planning-loop.ps1') -Repository $fixture.Project
        Assert-TestThrows { & (Join-Path $fixture.Project '.codex/scripts/build-loop.ps1') -Repository $fixture.Project -PmInputReader { throw 'PM must not be invoked for operational blockers.' } } 'Task attempts exhausted'
        $state = [IO.File]::ReadAllText((Join-Path $fixture.Project '.codex/state.json'),$fixture.Utf8) | ConvertFrom-Json -Depth 100
        $tasks = [IO.File]::ReadAllText((Join-Path $fixture.Project '.codex/tasks.json'),$fixture.Utf8) | ConvertFrom-Json -Depth 100
        Assert-TestEqual 0 ([int]$state.amendmentSequence) 'operational blocker never invokes PM'
        Assert-TestTrue ($null -eq $state.activeAmendment) 'operational blocker creates no amendment state'
        Assert-TestEqual 2 ([int]$tasks.tasks[0].attemptCount) 'operational retries stop at configured limit'
        Assert-TestEqual 'blocked' ([string]$tasks.tasks[0].status) 'exhausted operational task is blocked'
    } finally { Remove-TestDirectory $fixture.Temporary }

    $fixture = New-PmDecisionFixture
    try {
        $env:RALPH_FAKE_GH_STATE = $fixture.ProviderState
        Remove-Item Env:RALPH_FAKE_SEMANTIC_BLOCKER -ErrorAction SilentlyContinue
        Remove-Item Env:RALPH_FAKE_OPERATIONAL_BLOCKER -ErrorAction SilentlyContinue
        $env:RALPH_FAKE_BUG_DISPOSITION = '1'
        & (Join-Path $fixture.Project '.codex/scripts/planning-loop.ps1') -Repository $fixture.Project
        & (Join-Path $fixture.Project '.codex/scripts/build-loop.ps1') -Repository $fixture.Project
        & (Join-Path $fixture.Project '.codex/scripts/audit-loop.ps1') -Repository $fixture.Project -PmInputReader { param($Analysis) 'OPTION-0001' }
        $state = [IO.File]::ReadAllText((Join-Path $fixture.Project '.codex/state.json'),$fixture.Utf8) | ConvertFrom-Json -Depth 100
        $bugs = [IO.File]::ReadAllText((Join-Path $fixture.Project '.codex/bugs.json'),$fixture.Utf8) | ConvertFrom-Json -Depth 100
        Assert-TestEqual 'complete' ([string]$state.stage) 'disposition-only decision completes the audit workflow'
        Assert-TestTrue ($null -eq $state.activeAmendment) 'applied disposition clears active amendment state'
        Assert-TestEqual 'verified' ([string]$bugs.bugs[0].status) 'disposition marks the bug verified'
        Assert-TestEqual 'superseded' ([string]$bugs.bugs[0].disposition) 'selected bug disposition is persisted'
        Assert-TestEqual 'AMEND-0001' ([string]$bugs.bugs[0].amendmentId) 'bug records its decision amendment identity'
        Assert-TestTrue (-not [string]::IsNullOrWhiteSpace([string]$bugs.bugs[0].dispositionEvidence)) 'bug disposition retains decision evidence'
        Assert-TestTrue (-not (Test-Path -LiteralPath (Join-Path $fixture.Temporary 'worktrees/fixture_project/AMEND-0001'))) 'disposition-only amendment worktree is cleaned'
    } finally { Remove-TestDirectory $fixture.Temporary }
}
finally {
    $env:PATH = $oldPath
    $env:RALPH_FAKE_GH_STATE = $oldProviderState
    $env:RALPH_FAKE_SEMANTIC_BLOCKER = $oldSemantic
    $env:RALPH_FAKE_OPERATIONAL_BLOCKER = $oldOperational
    $env:RALPH_FAKE_BUG_DISPOSITION = $oldDisposition
}

Write-Host "PM decision tests passed: $script:RalphTestCount assertions"
