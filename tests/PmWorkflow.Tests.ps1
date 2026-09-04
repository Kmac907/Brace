. (Join-Path $PSScriptRoot 'TestSupport.ps1')
$sourceRoot=Split-Path $PSScriptRoot -Parent;$template=Join-Path $sourceRoot 'template';$temporary=New-TestDirectory
$oldPath=$env:PATH;$oldProviderState=$env:RALPH_FAKE_GH_STATE;$oldSemantic=$env:RALPH_FAKE_SEMANTIC_BLOCKER
try {
    $project=Join-Path $temporary 'project';$remote=Join-Path $temporary 'remote.git';[void][IO.Directory]::CreateDirectory($project)
    foreach($entry in Get-ChildItem $template -Force){Copy-Item $entry.FullName $project -Recurse -Force}
    $utf8=[Text.UTF8Encoding]::new($false,$true);$configPath=Join-Path $project '.codex/workflow.json';$config=[IO.File]::ReadAllText($configPath,$utf8)|ConvertFrom-Json -Depth 50;$config.github.repository='fixture/project';$config.maximumConcurrentBuilders=2;$config.maximumConcurrentFixers=2;$config.worktreeRoot=Join-Path $temporary 'worktrees';[IO.File]::WriteAllText($configPath,($config|ConvertTo-Json -Depth 50),$utf8)
    [IO.File]::WriteAllText((Join-Path $project 'requirements.md'),"# Requirements`n`n- REQ-FUNC-001: Create a feature file.",$utf8)
    & git init --bare $remote|Out-Null;& git init -b main $project|Out-Null;& git -C $project config user.name 'Worktree Ralph Test';& git -C $project config user.email 'test@example.invalid';& git -C $project add --all;& git -C $project commit -m 'Initial project'|Out-Null
    $hosted='https://github.com/fixture/project.git';& git -C $project config "url.$remote.insteadOf" $hosted;& git -C $project remote add origin $hosted;& git -C $project push --set-upstream origin main|Out-Null;if($LASTEXITCODE-ne0){throw 'Unable to initialize PM fixture.'}
    $env:PATH=(Join-Path $PSScriptRoot 'fixtures/fake-bin')+[IO.Path]::PathSeparator+$oldPath;$env:RALPH_FAKE_GH_STATE=Join-Path $temporary 'pull-requests.json';$env:RALPH_FAKE_SEMANTIC_BLOCKER='1'
    & (Join-Path $project '.codex/scripts/planning-loop.ps1') -Repository $project
    & (Join-Path $project '.codex/scripts/build-loop.ps1') -Repository $project -PmInputReader { param($Analysis) 'OPTION-0001' }
    & (Join-Path $project '.codex/scripts/audit-loop.ps1') -Repository $project -PmInputReader { param($Analysis) 'OPTION-0001' }
    $state=[IO.File]::ReadAllText((Join-Path $project '.codex/state.json'),$utf8)|ConvertFrom-Json -Depth 100;$tasks=[IO.File]::ReadAllText((Join-Path $project '.codex/tasks.json'),$utf8)|ConvertFrom-Json -Depth 100
    Assert-TestEqual 'complete' ([string]$state.stage) 'PM workflow completes'
    Assert-TestEqual 1 ([int]$state.amendmentSequence) 'one semantic amendment recorded'
    Assert-TestTrue ($null-eq$state.activeAmendment) 'active amendment clears after integration'
    Assert-TestEqual 1 @($state.acceptedIntegrationShas).Count 'accepted integration merge identity retained'
    Assert-TestEqual 2 @($tasks.tasks).Count 'follow-up task appended'
    Assert-TestEqual 'superseded' ([string]$tasks.tasks[0].status) 'untouched blocked task superseded'
    Assert-TestEqual 'integrated' ([string]$tasks.tasks[1].status) 'follow-up task integrated'
    Assert-TestEqual 'AMEND-0001' ([string]$tasks.tasks[1].amendmentId) 'follow-up task linked to amendment'
    Assert-TestTrue (([IO.File]::ReadAllText((Join-Path $project 'requirements.md'),$utf8)).Contains('REQ-FUNC-002')) 'final main contains approved requirement amendment'
    $prs=[IO.File]::ReadAllText($env:RALPH_FAKE_GH_STATE,$utf8)|ConvertFrom-Json -Depth 100
    Assert-TestTrue (@($prs|Where-Object headRefName -eq 'worktree/AMEND-0001').Count-eq1) 'contract amendment used its own provider pull request'
    Assert-TestTrue ([IO.File]::Exists((Join-Path $project '.codex/results/AMEND-0001-analysis.json'))) 'PM analysis is durable'
    Assert-TestTrue ([IO.File]::Exists((Join-Path $project '.codex/assignments/AMEND-0001-attempt-001.json'))) 'PM amendment assignment is durable'
    Assert-TestTrue ([IO.File]::Exists((Join-Path $project '.codex/results/AMEND-0001-attempt-001.json'))) 'PM amendment result is durable'
    Assert-TestTrue (-not[IO.Directory]::Exists((Join-Path $temporary 'worktrees'))) 'PM workflow cleans all owned worktrees'
} finally {$env:PATH=$oldPath;$env:RALPH_FAKE_GH_STATE=$oldProviderState;$env:RALPH_FAKE_SEMANTIC_BLOCKER=$oldSemantic;Remove-TestDirectory $temporary}
Write-Host "PM workflow tests passed: $script:RalphTestCount assertions"
