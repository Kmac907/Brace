. (Join-Path $PSScriptRoot 'TestSupport.ps1')
$sourceRoot = Split-Path $PSScriptRoot -Parent
$common = Join-Path $sourceRoot 'template\.codex\scripts\common.ps1'
. $common

$temporary = New-TestDirectory
$oldPath = $env:PATH
$oldProviderState = $env:RALPH_FAKE_GH_STATE
try {
    $repository = Join-Path $temporary 'repository'
    $remote = Join-Path $temporary 'remote.git'
    [void][IO.Directory]::CreateDirectory($repository)
    Copy-Item (Join-Path $sourceRoot 'template\.codex') (Join-Path $repository '.codex') -Recurse
    Copy-Item (Join-Path $sourceRoot 'template\.gitignore') (Join-Path $repository '.gitignore')
    $configPath = Join-Path $repository '.codex\workflow.json'
    $configJson = Read-RalphJson $configPath
    $configJson.github.repository = 'fixture/project'
    $configJson.worktreeRoot = Join-Path $temporary 'worktrees'
    Write-RalphTextAtomic $configPath ($configJson | ConvertTo-Json -Depth 30)

    [void](Invoke-RalphNative git @('init','--bare',$remote) $temporary)
    [void](Invoke-RalphNative git @('init','-b','main') $repository)
    [void](Invoke-RalphNative git @('config','user.name','Worktree Ralph Test') $repository)
    [void](Invoke-RalphNative git @('config','user.email','test@example.invalid') $repository)
    Write-RalphTextAtomic (Join-Path $repository 'requirements.md') '# REQ-FUNC-001'
    Write-RalphTextAtomic (Join-Path $repository 'plan.md') '# Plan'
    [void](Invoke-RalphNative git @('add','--all') $repository)
    [void](Invoke-RalphNative git @('commit','-m','base') $repository)
    $hosted = 'https://github.com/fixture/project.git'
    [void](Invoke-RalphNative git @('config',"url.$remote.insteadOf",$hosted) $repository)
    [void](Invoke-RalphNative git @('remote','add','origin',$hosted) $repository)
    [void](Invoke-RalphNative git @('push','-u','origin','main') $repository)

    $configuration = Get-RalphConfiguration $repository
    $state = New-RalphState $repository $configuration 'fixture/project'
    $baseSha = (Invoke-RalphNative git @('rev-parse','HEAD') $repository).Output.Trim()
    $state.targetBaseSha = $baseSha
    $state.requirementsHash = Get-RalphGitBlobIdentity $repository $baseSha 'requirements.md'
    $state.planHash = Get-RalphGitBlobIdentity $repository $baseSha 'plan.md'
    Assert-RalphStateIdentity $state $repository $configuration
    Assert-TestThrows { $state.repository='wrong/repository'; Assert-RalphStateIdentity $state $repository $configuration } 'repository identity'
    $state.repository='fixture/project'
    $state.configurationHash='sha256:'+('1'*64)
    Assert-TestThrows { Assert-RalphStateIdentity $state $repository $configuration } 'workflow.json changed'
    $state.configurationHash=Get-RalphFileHash $configPath

    Write-RalphTextAtomic (Join-Path $repository 'requirements.md') '# REQ-FUNC-001 changed'
    Assert-TestThrows { Assert-RalphPlanDrift $state $repository -RequirePlan } 'requirements.md changed'
    Write-RalphTextAtomic (Join-Path $repository 'requirements.md') '# REQ-FUNC-001'

    $task=[pscustomobject]@{taskId='TASK-0001';title='A';description='A';status='pending';requirementIds=@('REQ-FUNC-001');planSections=@('Plan');dependencies=@();allowedPaths=@('src/**');exclusiveResources=@();acceptanceCriteria=@('works');checks=@('test');attemptCount=0;branch=$null;worktree=$null;baseSha=$null;resultSha=$null;pullRequest=$null;lastError=$null}
    $definitionHash=Get-RalphDefinitionHash @($task) task
    $ledger=[pscustomobject]@{schemaVersion='1.0';revision=0;planHash=$state.planHash;definitionHash=$definitionHash;status='ready';tasks=@($task)}
    $state.taskDefinitionHash=$definitionHash
    Assert-RalphLedgerIdentity $state $ledger task
    $task.title='changed'
    Assert-TestThrows { Assert-RalphLedgerIdentity $state $ledger task } 'definitions changed'
    $task.title='A'

    $paths=Get-RalphPaths $repository
    [void][IO.Directory]::CreateDirectory($paths.Assignments)
    $recordPath=Get-RalphAttemptPath $paths assignment 'TASK-0001' 1
    Write-RalphImmutableJson $recordPath ([ordered]@{identity='TASK-0001';attempt=1})
    Assert-TestThrows { Write-RalphImmutableJson $recordPath ([ordered]@{identity='TASK-0001';attempt=2}) } 'Immutable attempt record'
    $complexA=[pscustomobject]@{allowedPaths=@('src/*/generated.rs');exclusiveResources=@()}
    $complexB=[pscustomobject]@{allowedPaths=@('src/core/**');exclusiveResources=@()}
    Assert-TestTrue (Test-RalphItemsConflict $complexA $complexB) 'complex globs serialize conservatively'

    $integrationSha=Ensure-RalphIntegrationBranch $repository $configuration $state
    $state.integrationSha=$integrationSha
    [void](Invoke-RalphNative git @('switch','ralph/integration') $repository)
    Write-RalphTextAtomic (Join-Path $repository 'owned.txt') 'owned'
    [void](Invoke-RalphNative git @('add','owned.txt') $repository)
    [void](Invoke-RalphNative git @('commit','-m','external integration commit') $repository)
    $unknownSha=(Invoke-RalphNative git @('rev-parse','HEAD') $repository).Output.Trim()
    [void](Invoke-RalphNative git @('push','origin','ralph/integration') $repository)
    [void](Invoke-RalphNative git @('switch','main') $repository)
    Assert-TestThrows { Ensure-RalphIntegrationBranch $repository $configuration $state } 'unowned commits'
    Assert-TestEqual $unknownSha (Ensure-RalphIntegrationBranch $repository $configuration $state @($unknownSha)) 'known provider merge accepted'

    $worktree=New-RalphWorktree $repository $configuration 'TASK-0001' 'worktree/TASK-0001' $baseSha
    Write-RalphTextAtomic (Join-Path $worktree 'untracked.txt') 'dirty'
    Assert-TestThrows { New-RalphWorktree $repository $configuration 'TASK-0001' 'worktree/TASK-0001' $baseSha } 'uncommitted changes'
    [IO.File]::Delete((Join-Path $worktree 'untracked.txt'))
    Remove-RalphWorktree $repository $configuration 'TASK-0001' 'worktree/TASK-0001'

    $env:PATH=(Join-Path $sourceRoot 'tests\fixtures\fake-bin')+[IO.Path]::PathSeparator+$oldPath
    $env:RALPH_FAKE_GH_STATE=Join-Path $temporary 'prs.json'
    $different='1'*40
    Write-RalphTextAtomic $env:RALPH_FAKE_GH_STATE (@([ordered]@{number=1;url='https://example.invalid/1';state='OPEN';headRefName='worktree/TASK-0001';headRefOid=$different;baseRefName='ralph/integration';baseRefOid=$integrationSha;mergeCommit=$null})|ConvertTo-Json -Depth 20 -AsArray)
    Assert-TestThrows { Get-RalphPullRequest $repository $configuration 'worktree/TASK-0001' 'ralph/integration' ('2'*40) } 'different head SHA'
    $badMerge='3'*40
    Write-RalphTextAtomic $env:RALPH_FAKE_GH_STATE (@([ordered]@{number=2;url='https://example.invalid/2';state='MERGED';headRefName='worktree/TASK-0001';headRefOid=$different;baseRefName='ralph/integration';baseRefOid=$integrationSha;mergeCommit=[ordered]@{oid=$badMerge}})|ConvertTo-Json -Depth 20 -AsArray)
    $badPr=Get-RalphPullRequest $repository $configuration 'worktree/TASK-0001' 'ralph/integration' $different '2'
    Assert-TestThrows { Complete-RalphPullRequest $repository $configuration $badPr } 'Command failed|merge result'

    $azure=[pscustomobject]@{pullRequestId=7;url='https://example.invalid/7';status='completed';sourceRefName='refs/heads/worktree/TASK-0001';targetRefName='refs/heads/ralph/integration';lastMergeSourceCommit=[pscustomobject]@{commitId='4'*40};lastMergeTargetCommit=[pscustomobject]@{commitId='5'*40};lastMergeCommit=[pscustomobject]@{commitId='6'*40}}
    $azureRecord=ConvertTo-RalphPullRequestRecord $azure azure_devops 'org|project|repo'
    Assert-TestEqual ('4'*40) ([string]$azureRecord.headSha) 'Azure source SHA recorded'
    Assert-TestEqual ('5'*40) ([string]$azureRecord.baseSha) 'Azure base SHA recorded'

    [void](Invoke-RalphNative git @('switch','main') $repository)
    Write-RalphTextAtomic (Join-Path $repository 'drift.txt') 'drift'
    [void](Invoke-RalphNative git @('add','drift.txt') $repository)
    [void](Invoke-RalphNative git @('commit','-m','target drift') $repository)
    [void](Invoke-RalphNative git @('push','origin','main') $repository)
    Assert-TestThrows { Assert-RalphTargetDrift $repository $configuration $state } 'Target branch advanced'

    [void](Invoke-RalphNative git @('push','origin','--delete','ralph/integration') $repository)
    [void](Invoke-RalphNative git @('branch','-D','ralph/integration') $repository)
    $state.stage='complete';$state.stageStatus='complete';$state.finalMergeSha=(Invoke-RalphNative git @('rev-parse','HEAD') $repository).Output.Trim()
    Save-RalphState $state $paths
    Write-RalphTextAtomic $paths.Tasks '{}';Write-RalphTextAtomic $paths.Bugs '{}'
    Reset-RalphCompletedWorkflow $repository $configuration $state
    Assert-TestTrue (-not(Test-Path $paths.State)) 'new workflow removes completed state'
    Assert-TestTrue (-not(Test-Path $paths.Tasks)) 'new workflow removes completed task ledger'
    $archivedRecord=Join-Path $paths.Logs "completed-$($state.finalMergeSha.Substring(0,12))\assignments\TASK-0001-attempt-001.json"
    Assert-TestTrue (Test-Path $archivedRecord) 'new workflow archives immutable attempt records'

    $auditScript=Read-RalphText (Join-Path $sourceRoot 'template\.codex\scripts\audit-loop.ps1')
    Assert-TestTrue ($auditScript.Contains("status='ready_to_publish'") -and -not $auditScript.Contains("if (`$null -eq `$bug.pullRequest) {`n            `$bug.status = 'verified'")) 'fixed bugs cannot verify without an exact PR'
}
finally {
    $env:PATH=$oldPath
    $env:RALPH_FAKE_GH_STATE=$oldProviderState
    Remove-TestDirectory $temporary
}

Write-Host "Reconciliation tests passed: $script:RalphTestCount assertions"
