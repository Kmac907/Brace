. (Join-Path $PSScriptRoot 'TestSupport.ps1')
$sourceRoot=Split-Path $PSScriptRoot -Parent
. (Join-Path $sourceRoot 'template/.codex/scripts/common.ps1')
$temporary=New-TestDirectory
try {
    $paths=[pscustomobject]@{
        State=Join-Path $temporary 'state.json'
        Tasks=Join-Path $temporary 'tasks.json'
        Bugs=Join-Path $temporary 'bugs.json'
        Schemas=Join-Path $sourceRoot 'template/.codex/schemas'
    }
    $now=[DateTimeOffset]::UtcNow.ToString('O')
    $state=[ordered]@{schemaVersion='1.0';revision=0;repositoryRoot=$temporary;provider='github';repository='owner/repo';remote='origin';remoteUrl='https://github.com/owner/repo.git';targetBranch='main';targetBaseSha=$null;integrationBranch='ralph/integration';configurationHash=('sha256:'+('0'*64));taskDefinitionHash=$null;bugDefinitionHash=$null;stage='requirements';stageStatus='not_started';requirementsHash=$null;planHash=$null;integrationSha=$null;finalMergeSha=$null;blocker=$null;createdAt=$now;updatedAt=$now}
    $task=[ordered]@{taskId='TASK-0001';title='Old task';description='Migration fixture';status='pending';requirementIds=@('REQ-FUNC-001');planSections=@('Feature');dependencies=@();allowedPaths=@('src/**');exclusiveResources=@();acceptanceCriteria=@('works');checks=@('test');attemptCount=0;branch=$null;worktree=$null;baseSha=$null;resultSha=$null;pullRequest=$null;lastError=$null}
    $tasks=[ordered]@{schemaVersion='1.0';revision=0;planHash=('sha256:'+('1'*64));definitionHash=$null;status='ready';tasks=@($task)}
    $bug=[ordered]@{bugId='BUG-0001';title='Old bug';severity='low';category='fixture';status='open';disposition=$null;requirementIds=@('REQ-FUNC-001');description='fixture';evidence='fixture';actualBehavior='old';requiredBehavior='new';impact='fixture';requiredCorrection='fix';acceptanceTest='test';dependencies=@();allowedPaths=@('src/**');exclusiveResources=@();attemptCount=0;branch=$null;worktree=$null;baseSha=$null;resultSha=$null;pullRequest=$null;lastError=$null}
    $bugs=[ordered]@{schemaVersion='1.0';revision=0;auditSha=$null;definitionHash=$null;status='ready';bugs=@($bug)}
    Write-RalphTextAtomic $paths.State ($state|ConvertTo-Json -Depth 50)
    Write-RalphTextAtomic $paths.Tasks ($tasks|ConvertTo-Json -Depth 50)
    Write-RalphTextAtomic $paths.Bugs ($bugs|ConvertTo-Json -Depth 50)
    Update-RalphWorkflowStateSchema $temporary $paths
    $migratedState=Read-RalphJson $paths.State (Join-Path $paths.Schemas 'state.schema.json')
    $migratedTasks=Read-RalphJson $paths.Tasks (Join-Path $paths.Schemas 'tasks.schema.json')
    $migratedBugs=Read-RalphJson $paths.Bugs (Join-Path $paths.Schemas 'bugs.schema.json')
    Assert-TestEqual '1.2' ([string]$migratedState.schemaVersion) 'state schema migrated'
    Assert-TestEqual 0 @($migratedState.acceptedIntegrationShas).Count 'accepted merge set initialized'
    Assert-TestEqual '1.1' ([string]$migratedTasks.schemaVersion) 'task schema migrated'
    Assert-TestTrue ($null-eq$migratedTasks.tasks[0].amendmentId) 'existing task remains unamended'
    Assert-TestEqual '1.2' ([string]$migratedBugs.schemaVersion) 'bug schema migrated'
    Assert-TestTrue ($null-eq$migratedBugs.bugs[0].amendmentId) 'existing bug remains unamended'
    Assert-TestTrue ($null-eq$migratedBugs.bugs[0].dispositionEvidence) 'bug disposition evidence initialized'
} finally {Remove-TestDirectory $temporary}
Write-Host "Migration tests passed: $script:RalphTestCount assertions"