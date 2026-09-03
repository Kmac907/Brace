param([string]$Repository = (Get-Location).Path)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

function Save-BuildLedger {
    param($Ledger, $Paths)
    $Ledger.revision = [int]$Ledger.revision + 1
    Write-RalphJsonAtomic $Paths.Tasks $Ledger (Join-Path $Paths.Schemas 'tasks.schema.json')
}

function Get-KnownTaskMergeShas {
    param($Ledger)
    @($Ledger.tasks | Where-Object { $null -ne $_.pullRequest -and $_.pullRequest.mergeSha } | ForEach-Object { [string]$_.pullRequest.mergeSha })
}

$root = Get-RalphRepositoryRoot $Repository
$configuration = Get-RalphConfiguration $root
Assert-RalphPrerequisites $configuration -RequireCodex
if (-not (Get-Command Start-ThreadJob -ErrorAction SilentlyContinue)) { throw 'PowerShell ThreadJob support is required for concurrent builders.' }
$paths = Initialize-RalphStateFiles $root $configuration
$lock = Enter-RalphWorkflowLock $paths.Lock
$state = $null
$tasks = $null

try {
    $state = Read-RalphJson $paths.State (Join-Path $paths.Schemas 'state.schema.json')
    $tasks = Read-RalphJson $paths.Tasks (Join-Path $paths.Schemas 'tasks.schema.json')
    Assert-RalphStateIdentity $state $root $configuration
    Assert-RalphPlanDrift $state $root -RequirePlan
    Assert-RalphLedgerIdentity $state $tasks task
    Assert-RalphGraph @($tasks.tasks) task
    [void](Assert-RalphTargetDrift $root $configuration $state)

    if ([string]$tasks.status -ceq 'complete' -and [string]$state.stage -eq 'audit') {
        Show-RalphStatus $state $tasks
        Write-Host 'BUILD COMPLETE: the audit loop may run.'
        return
    }
    if ([string]$tasks.status -notin @('ready','active','blocked')) { throw 'Planning has not produced a buildable task queue.' }
    if ([string]$state.stage -notin @('build','blocked')) { throw "The workflow is at stage $($state.stage), not build." }

    $state.stage='build'; $state.stageStatus='running'; $state.blocker=$null; $tasks.status='active'
    Save-RalphState $state $paths

    # Recover completed agent attempts before scheduling replacements.
    foreach ($task in @($tasks.tasks | Where-Object status -eq 'active')) {
        $attemptResult = Read-RalphAttemptResult $paths ([string]$task.taskId) ([int]$task.attemptCount)
        if ($null -ne $attemptResult -and [bool]$attemptResult.succeeded) { $task.status='result_ready' }
        else { $task.status='pending'; $task.lastError=if($null -eq $attemptResult){'Interrupted before a durable result was produced.'}else{[string]$attemptResult.error} }
    }

    # Recover pull requests created or merged immediately before a crash.
    foreach ($task in @($tasks.tasks | Where-Object status -in @('result_ready','submitted'))) {
        if (-not $task.resultSha) { $task.status='pending'; continue }
        $existing = Get-RalphPullRequest $root $configuration ([string]$task.branch) ([string]$configuration.integrationBranch) ([string]$task.resultSha)
        if ($null -ne $existing) {
            $merged = Complete-RalphPullRequest $root $configuration $existing
            $task.pullRequest=$merged; $task.status='integrated'; $task.lastError=$null; $state.integrationSha=[string]$merged.mergeSha
            Save-BuildLedger $tasks $paths; Save-RalphState $state $paths
            try { Remove-RalphMergedAssignment $root $configuration ([string]$task.taskId) ([string]$task.branch) $merged } catch { Write-Warning $_.Exception.Message }
        }
    }
    Save-BuildLedger $tasks $paths
    $state.integrationSha = Ensure-RalphIntegrationBranch $root $configuration $state (Get-KnownTaskMergeShas $tasks)
    Save-RalphState $state $paths

    while (@($tasks.tasks | Where-Object status -ne 'integrated').Count -gt 0) {
        Assert-RalphPlanDrift $state $root -RequirePlan
        Assert-RalphLedgerIdentity $state $tasks task
        [void](Assert-RalphTargetDrift $root $configuration $state)
        $state.integrationSha = Ensure-RalphIntegrationBranch $root $configuration $state (Get-KnownTaskMergeShas $tasks)

        # Finish durable successful results first; no second builder attempt is needed.
        $readyResults = @($tasks.tasks | Where-Object status -in @('result_ready','submitted'))
        foreach ($task in $readyResults) {
            $attemptResult = Read-RalphAttemptResult $paths ([string]$task.taskId) ([int]$task.attemptCount)
            if ($null -eq $attemptResult -or -not [bool]$attemptResult.succeeded) { $task.status='pending'; continue }
            $result = $attemptResult.result
            Write-Host "VERIFYING TASK: $($task.taskId) attempt $($task.attemptCount)"
            try {
                Assert-RalphPlanDrift $state $root -RequirePlan; Assert-RalphLedgerIdentity $state $tasks task; [void](Assert-RalphTargetDrift $root $configuration $state)
                $commit = Assert-RalphAssignmentCommit ([string]$task.worktree) ([string]$task.baseSha) $task
                if ([string]$result.commitSha -cne [string]$commit.Head) { throw 'Builder result commit SHA does not match the worktree HEAD.' }
                $task.resultSha=[string]$commit.Head; $task.status='result_ready'; Save-BuildLedger $tasks $paths
                $context='Verify only this task:'+[Environment]::NewLine+($task|ConvertTo-Json -Depth 50)+[Environment]::NewLine+'Builder result:'+[Environment]::NewLine+($result|ConvertTo-Json -Depth 50)
                $verification=Invoke-RalphRole $root ([string]$task.worktree) 'verifier' $context 'verifier-result.schema.json' 'read-only'
                if(-not[bool]$verification.approved){throw "Focused verification failed: $(@($verification.findings)-join'; ')"}
                Assert-RalphPlanDrift $state $root -RequirePlan; Assert-RalphLedgerIdentity $state $tasks task; [void](Assert-RalphTargetDrift $root $configuration $state)
                $state.integrationSha=Ensure-RalphIntegrationBranch $root $configuration $state (Get-KnownTaskMergeShas $tasks)
                Write-Host "PUBLISHING TASK PR: $($task.taskId) at $($task.resultSha)"
                $merged=Publish-RalphAssignment $root ([string]$task.worktree) $configuration $task task
                Write-Host "TASK PR MERGED: $($task.taskId) -> $($merged.mergeSha)"
                $task.pullRequest=$merged; $task.status='integrated'; $task.lastError=$null; $state.integrationSha=[string]$merged.mergeSha
                Save-BuildLedger $tasks $paths; Save-RalphState $state $paths
                try { Remove-RalphMergedAssignment $root $configuration ([string]$task.taskId) ([string]$task.branch) $merged } catch { Write-Warning $_.Exception.Message }
            } catch { $task.status=if($task.resultSha){'result_ready'}else{'pending'}; $task.lastError=$_.Exception.Message; Write-Warning "$($task.taskId) attempt $($task.attemptCount) failed: $($task.lastError)" }
        }
        Save-BuildLedger $tasks $paths
        if (@($tasks.tasks | Where-Object status -ne 'integrated').Count -eq 0) { break }

        $exhausted=@($tasks.tasks|Where-Object{$_.status-ne'integrated'-and[int]$_.attemptCount-ge[int]$configuration.maximumTaskAttempts})
        if($exhausted){foreach($task in $exhausted){$task.status='blocked'};$tasks.status='blocked';Save-BuildLedger $tasks $paths;throw "Task attempts exhausted: $(@($exhausted.taskId)-join', ')"}
        $wave=@(Select-RalphReadyItems @($tasks.tasks) task ([int]$configuration.maximumConcurrentBuilders))
        if(-not$wave){throw 'No dependency-ready, non-conflicting tasks remain. Inspect blocked dependencies and path ownership.'}
        $baseSha=[string]$state.integrationSha
        foreach($task in $wave){
            if(-not$task.branch){$task.branch="worktree/$($task.taskId)"}; if(-not$task.baseSha){$task.baseSha=$baseSha}
            $task.worktree=New-RalphWorktree $root $configuration ([string]$task.taskId) ([string]$task.branch) ([string]$task.baseSha) ([string]$task.resultSha)
            $task.attemptCount=[int]$task.attemptCount+1; $task.status='active'; $task.lastError=$null
            $assignment=[ordered]@{schemaVersion='1.0';identity=[string]$task.taskId;attempt=[int]$task.attemptCount;baseSha=[string]$task.baseSha;startingHead=(Invoke-RalphNative git @('-C',[string]$task.worktree,'rev-parse','HEAD')).Output.Trim();createdAt=[DateTimeOffset]::UtcNow.ToString('O');item=$task}
            Write-RalphImmutableJson (Get-RalphAttemptPath $paths assignment ([string]$task.taskId) ([int]$task.attemptCount)) $assignment
        }
        Save-BuildLedger $tasks $paths

        $jobs=foreach($task in $wave){
            $taskJson=$task|ConvertTo-Json -Depth 50 -Compress; $resultPath=Get-RalphAttemptPath $paths result ([string]$task.taskId) ([int]$task.attemptCount)
            Start-ThreadJob -Name ([string]$task.taskId) -ArgumentList @($PSScriptRoot,$root,[string]$task.worktree,$taskJson,$resultPath) -ScriptBlock {
                param($ScriptsPath,$RepositoryRoot,$Worktree,$TaskJson,$ResultPath)
                . (Join-Path $ScriptsPath 'common.ps1');$task=$TaskJson|ConvertFrom-Json -Depth 50;$context='Task assignment:'+[Environment]::NewLine+($task|ConvertTo-Json -Depth 50)
                try{$result=Invoke-RalphRole $RepositoryRoot $Worktree 'builder' $context 'builder-result.schema.json' 'workspace-write';$record=[ordered]@{schemaVersion='1.0';identity=[string]$task.taskId;attempt=[int]$task.attemptCount;succeeded=$true;result=$result;error=$null;completedAt=[DateTimeOffset]::UtcNow.ToString('O')}}catch{$record=[ordered]@{schemaVersion='1.0';identity=[string]$task.taskId;attempt=[int]$task.attemptCount;succeeded=$false;result=$null;error=$_.Exception.Message;completedAt=[DateTimeOffset]::UtcNow.ToString('O')}}
                Write-RalphImmutableJson $ResultPath $record;$record
            }
        }
        $outerTimeout=([int]$configuration.agentTimeoutMinutes*60)+[int]$configuration.agentCleanupGraceSeconds+30
        try {
            [void](Wait-Job $jobs -Timeout $outerTimeout)
            foreach($job in $jobs){$task=@($wave|Where-Object { $_.taskId -eq $job.Name })[0];if($job.State-ne'Completed'){Stop-Job $job -ErrorAction SilentlyContinue;$task.status='pending';$task.lastError='Builder did not stop after the bounded agent deadline.';continue};$record=Receive-Job $job;if($null-eq$record-or-not[bool]$record.succeeded){$task.status='pending';$task.lastError=if($null-eq$record){'Builder returned no durable result.'}else{[string]$record.error}}else{$task.status='result_ready'}}
        } finally { $jobs|ForEach-Object{if($_.State-ne'Completed'){Stop-Job $_ -ErrorAction SilentlyContinue};Remove-Job $_ -Force -ErrorAction SilentlyContinue} }
        Save-BuildLedger $tasks $paths; Show-RalphStatus $state $tasks
    }

    Write-Host 'BUILD: final drift check'
    Assert-RalphPlanDrift $state $root -RequirePlan; Assert-RalphLedgerIdentity $state $tasks task; [void](Assert-RalphTargetDrift $root $configuration $state)
    $state.integrationSha=Ensure-RalphIntegrationBranch $root $configuration $state (Get-KnownTaskMergeShas $tasks)
    Write-Host 'BUILD: create integration verifier worktree'
    $verificationWorktree=New-RalphAuditWorktree $root $configuration "$($configuration.remote)/$($configuration.integrationBranch)"
    try{Write-Host 'BUILD: run integration verifier';$context="Perform lightweight integration verification at exact SHA $($state.integrationSha). Confirm the build and direct smoke checks only.";$integrationVerification=Invoke-RalphRole $root $verificationWorktree 'verifier' $context 'verifier-result.schema.json' 'read-only';if(-not[bool]$integrationVerification.approved){throw "Lightweight integration verification failed: $(@($integrationVerification.findings)-join'; ')"}}finally{Write-Host 'BUILD: remove verifier worktree';Remove-RalphAuditWorktree $root $configuration}
    Assert-RalphPlanDrift $state $root -RequirePlan; Assert-RalphLedgerIdentity $state $tasks task; [void](Assert-RalphTargetDrift $root $configuration $state)
    $tasks.status='complete';Save-BuildLedger $tasks $paths;$state.stage='audit';$state.stageStatus='not_started';$state.blocker=$null;Save-RalphState $state $paths
    $attempts=@($tasks.tasks|ForEach-Object{[int]$_.attemptCount});$summary=[ordered]@{completedAt=[DateTimeOffset]::UtcNow.ToString('O');totalTasks=@($tasks.tasks).Count;integratedTasks=@($tasks.tasks|Where-Object { $_.status -eq 'integrated' }).Count;totalAttempts=($attempts|Measure-Object -Sum).Sum;averageAttempts=if($attempts.Count){[Math]::Round((($attempts|Measure-Object -Average).Average),2)}else{0};taskCommits=@($tasks.tasks.resultSha|Where-Object{$_});pullRequests=@($tasks.tasks.pullRequest|Where-Object{$_});integrationSha=$state.integrationSha;integrationVerification=$integrationVerification;remainingBlockers=@()}
    Write-RalphSummary $paths.BuildSummary $summary;Show-RalphStatus $state $tasks;Write-Host 'BUILD COMPLETE: all tasks are integrated. The audit loop may run.'
}
catch { if($null-ne$state){if($null-ne$tasks){try{Save-BuildLedger $tasks $paths}catch{}};Set-RalphBlocked $state $paths 'build' $null $_.Exception.Message 'Resolve the exact task, provider, drift, or environment blocker, then rerun build-loop.ps1.'};throw }
finally { if($null-ne$lock){$lock.Dispose()} }
