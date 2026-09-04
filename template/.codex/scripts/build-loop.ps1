param([string]$Repository = (Get-Location).Path, [scriptblock]$PmInputReader)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'pm.ps1')

function Save-BuildLedger { param($Ledger,$Paths) $Ledger.revision=[int]$Ledger.revision+1;Write-RalphJsonAtomic $Paths.Tasks $Ledger (Join-Path $Paths.Schemas 'tasks.schema.json') }
function Get-KnownTaskMergeShas { param($Ledger) @($Ledger.tasks|Where-Object{$_.pullRequest-and$_.pullRequest.mergeSha}|ForEach-Object{[string]$_.pullRequest.mergeSha}) }
function Reset-TaskAfterAmendment {
    param($Task,[string]$Root,$Configuration)
    if($Task.worktree-and[IO.Directory]::Exists([string]$Task.worktree)){
        $status=(Invoke-RalphNative git @('-C',[string]$Task.worktree,'status','--porcelain','--untracked-files=all')).Output
        if(-not[string]::IsNullOrWhiteSpace($status)){throw "Preserving $($Task.taskId): its pre-amendment worktree is dirty."}
        $head=(Invoke-RalphNative git @('-C',[string]$Task.worktree,'rev-parse','HEAD')).Output.Trim()
        if([string]$Task.status-ceq'superseded'){
            if($head-cne[string]$Task.baseSha-and(-not$Task.resultSha-or$head-cne[string]$Task.resultSha)){throw "Preserving $($Task.taskId): a superseded task has unrecorded work."}
            Remove-RalphWorktree $Root $Configuration ([string]$Task.taskId) ([string]$Task.branch);$Task.branch=$null;$Task.worktree=$null;$Task.baseSha=$null;$Task.resultSha=$null;$Task.pullRequest=$null;return
        }
        [void](Invoke-RalphNative git @('-C',[string]$Task.worktree,'fetch',[string]$Configuration.remote,'--prune'))
        [void](Invoke-RalphNative git @('-C',[string]$Task.worktree,'merge','--no-edit',"$($Configuration.remote)/$($Configuration.integrationBranch)"))
        $Task.baseSha=(Invoke-RalphNative git @('-C',[string]$Task.worktree,'rev-parse','HEAD')).Output.Trim()
    } else {$Task.branch=$null;$Task.worktree=$null;$Task.baseSha=$null}
    $Task.status='pending';$Task.resultSha=$null;$Task.pullRequest=$null;$Task.lastError=$null
}

$root=Get-RalphRepositoryRoot $Repository;$configuration=Get-RalphConfiguration $root;Assert-RalphPrerequisites $configuration -RequireCodex
if(-not(Get-Command Start-ThreadJob -ErrorAction SilentlyContinue)){throw 'PowerShell ThreadJob support is required for concurrent builders.'}
$paths=Initialize-RalphStateFiles $root $configuration;$lock=Enter-RalphWorkflowLock $paths.Lock;$state=$null;$tasks=$null;$bugs=$null
try {
    $state=Read-RalphJson $paths.State (Join-Path $paths.Schemas 'state.schema.json');$tasks=Read-RalphJson $paths.Tasks (Join-Path $paths.Schemas 'tasks.schema.json');$bugs=Read-RalphJson $paths.Bugs (Join-Path $paths.Schemas 'bugs.schema.json')
    Assert-RalphStateIdentity $state $root $configuration
    if($state.activeAmendment){$state.stage=[string]$state.activeAmendment.sourceStage;$resolution=Invoke-RalphPmResolution $root $configuration $state $paths $tasks $bugs ([string]$state.activeAmendment.sourceStage) ([string]$state.activeAmendment.sourceKind) ([string]$state.activeAmendment.sourceIdentity) $state.activeAmendment.blocker $PmInputReader;if([string]$resolution.ResumeStage-ceq'audit'){Show-RalphStatus $state $tasks $bugs;Write-Host 'PM AMENDMENT COMPLETE: resume the audit loop.';return}}
    Assert-RalphPlanDrift $state $root -RequirePlan;Assert-RalphLedgerIdentity $state $tasks task;Assert-RalphGraph @($tasks.tasks) task;[void](Assert-RalphTargetDrift $root $configuration $state)
    if([string]$tasks.status-ceq'complete'-and[string]$state.stage-eq'audit'){Show-RalphStatus $state $tasks;Write-Host 'BUILD COMPLETE: the audit loop may run.';return}
    if([string]$tasks.status-notin@('ready','active','blocked')){throw 'Planning has not produced a buildable task queue.'};if([string]$state.stage-notin@('build','blocked')){throw "The workflow is at stage $($state.stage), not build."}
    $state.stage='build';$state.stageStatus='running';$state.blocker=$null;$tasks.status='active';Save-RalphState $state $paths

    foreach($task in @($tasks.tasks|Where-Object status -eq 'active')){
        $attemptResult=Read-RalphAttemptResult $paths ([string]$task.taskId) ([int]$task.attemptCount)
        if($null-eq$attemptResult){$attemptResult=Recover-RalphCommittedAttempt $root $paths $task task}
        if($attemptResult-and[bool]$attemptResult.succeeded){$task.status='result_ready'}else{$task.status='pending';$task.lastError=if($null-eq$attemptResult){'Interrupted before a durable result or commit was produced.'}else{[string]$attemptResult.error}}
    }
    foreach($task in @($tasks.tasks|Where-Object status -in @('result_ready','verified_ready','submitted'))){if(-not$task.resultSha){continue};$existing=Get-RalphPullRequest $root $configuration ([string]$task.branch) ([string]$configuration.integrationBranch) ([string]$task.resultSha);if($existing){$merged=Complete-RalphPullRequest $root $configuration $existing;$task.pullRequest=$merged;$task.status='integrated';$task.lastError=$null;$state.integrationSha=[string]$merged.mergeSha;Save-BuildLedger $tasks $paths;Save-RalphState $state $paths;try{Remove-RalphMergedAssignment $root $configuration ([string]$task.taskId) ([string]$task.branch) $merged}catch{Write-Warning $_.Exception.Message}}}
    Save-BuildLedger $tasks $paths;$state.integrationSha=Ensure-RalphIntegrationBranch $root $configuration $state (Get-KnownTaskMergeShas $tasks);Save-RalphState $state $paths

    while(@($tasks.tasks|Where-Object status -notin @('integrated','superseded')).Count-gt0){
        Assert-RalphPlanDrift $state $root -RequirePlan;Assert-RalphLedgerIdentity $state $tasks task;[void](Assert-RalphTargetDrift $root $configuration $state);$state.integrationSha=Ensure-RalphIntegrationBranch $root $configuration $state (Get-KnownTaskMergeShas $tasks)
        $amendmentHandled=$false
        foreach($task in @($tasks.tasks|Where-Object status -eq 'result_ready')){
            $attemptResult=Read-RalphAttemptResult $paths ([string]$task.taskId) ([int]$task.attemptCount)
            if($null-eq$attemptResult-or-not[bool]$attemptResult.succeeded){$task.status='pending';$task.lastError=if($null-eq$attemptResult){'Durable builder result is missing.'}else{[string]$attemptResult.error};continue}
            $result=$attemptResult.result
            if([string]$result.status-ceq'blocked'){
                $blocker=ConvertTo-RalphStructuredBlocker $result.blocker 'build' ([string]$task.taskId)
                if(Test-RalphSemanticBlocker $blocker){
                    [void](Invoke-RalphPmResolution $root $configuration $state $paths $tasks $bugs 'build' 'task' ([string]$task.taskId) $blocker $PmInputReader)
                    Reset-TaskAfterAmendment $task $root $configuration;Save-BuildLedger $tasks $paths;$amendmentHandled=$true;break
                }
                $task.status='pending';$task.lastError=[string]$blocker.message
            }
        }
        if($amendmentHandled){continue}
        Save-BuildLedger $tasks $paths

        foreach($task in @($tasks.tasks|Where-Object status -eq 'result_ready')){
            $attemptResult=Read-RalphAttemptResult $paths ([string]$task.taskId) ([int]$task.attemptCount);$result=$attemptResult.result
            try {
                Assert-RalphPlanDrift $state $root -RequirePlan;Assert-RalphLedgerIdentity $state $tasks task;[void](Assert-RalphTargetDrift $root $configuration $state)
                $commit=Assert-RalphAssignmentCommit ([string]$task.worktree) ([string]$task.baseSha) $task
                if([string]$result.commitSha-cne[string]$commit.Head){throw 'Builder result commit SHA does not match the worktree HEAD.'}
                $task.resultSha=[string]$commit.Head;Save-BuildLedger $tasks $paths
                Write-Host "VERIFYING TASK: $($task.taskId) attempt $($task.attemptCount)"
                $context='Verify only this task:'+[Environment]::NewLine+($task|ConvertTo-Json -Depth 50)+[Environment]::NewLine+'Builder result:'+[Environment]::NewLine+($result|ConvertTo-Json -Depth 50)
                $verification=Invoke-RalphRole $root ([string]$task.worktree) 'verifier' $context 'verifier-result.schema.json' 'read-only'
                if(-not[bool]$verification.approved){
                    $vblocker=ConvertTo-RalphStructuredBlocker $verification.blocker 'build' ([string]$task.taskId)
                    if(Test-RalphSemanticBlocker $vblocker){
                        [void](Invoke-RalphPmResolution $root $configuration $state $paths $tasks $bugs 'build' 'verification' ([string]$task.taskId) $vblocker $PmInputReader)
                        Reset-TaskAfterAmendment $task $root $configuration;Save-BuildLedger $tasks $paths;$amendmentHandled=$true;break
                    }
                    $task.status='pending';$task.resultSha=$null;$task.lastError="Focused verification failed: $(@($verification.findings)-join'; ')";continue
                }
                $task.status='verified_ready';$task.lastError=$null;Save-BuildLedger $tasks $paths
            }
            catch {
                if($state.activeAmendment){throw}
                $task.status='pending';$task.resultSha=$null;$task.lastError=$_.Exception.Message
                Write-Warning "$($task.taskId) attempt $($task.attemptCount) failed verification: $($task.lastError)"
            }
        }
        if($amendmentHandled){continue}
        Save-BuildLedger $tasks $paths

        foreach($task in @($tasks.tasks|Where-Object status -eq 'verified_ready')){
            try {
                Assert-RalphPlanDrift $state $root -RequirePlan;Assert-RalphLedgerIdentity $state $tasks task;[void](Assert-RalphTargetDrift $root $configuration $state)
                $state.integrationSha=Ensure-RalphIntegrationBranch $root $configuration $state (Get-KnownTaskMergeShas $tasks)
                Write-Host "PUBLISHING TASK PR: $($task.taskId) at $($task.resultSha)"
                $merged=Publish-RalphAssignment $root ([string]$task.worktree) $configuration $task task
                Write-Host "TASK PR MERGED: $($task.taskId) -> $($merged.mergeSha)"
                $task.pullRequest=$merged;$task.status='integrated';$task.lastError=$null;$state.integrationSha=[string]$merged.mergeSha
                Save-BuildLedger $tasks $paths;Save-RalphState $state $paths
                try{Remove-RalphMergedAssignment $root $configuration ([string]$task.taskId) ([string]$task.branch) $merged}catch{Write-Warning $_.Exception.Message}
            }
            catch {$task.status='verified_ready';$task.lastError=$_.Exception.Message;Write-Warning "$($task.taskId) publish failed: $($task.lastError)"}
        }
        Save-BuildLedger $tasks $paths;if(@($tasks.tasks|Where-Object status -notin @('integrated','superseded')).Count-eq0){break}
        $exhausted=@($tasks.tasks|Where-Object{$_.status-notin@('integrated','superseded')-and[int]$_.attemptCount-ge[int]$configuration.maximumTaskAttempts});if($exhausted){foreach($task in $exhausted){$task.status='blocked'};$tasks.status='blocked';Save-BuildLedger $tasks $paths;throw "Task attempts exhausted: $(@($exhausted.taskId)-join', ')"}
        $wave=@(Select-RalphReadyItems @($tasks.tasks) task ([int]$configuration.maximumConcurrentBuilders));if(-not$wave){throw 'No dependency-ready, non-conflicting tasks remain. Inspect blocked dependencies and path ownership.'};$baseSha=[string]$state.integrationSha
        foreach($task in $wave){if(-not$task.branch){$task.branch="worktree/$($task.taskId)"};if(-not$task.baseSha){$task.baseSha=$baseSha};$task.worktree=New-RalphWorktree $root $configuration ([string]$task.taskId) ([string]$task.branch) ([string]$task.baseSha) ([string]$task.resultSha);$task.attemptCount=[int]$task.attemptCount+1;$task.status='active';$task.lastError=$null;$assignment=[ordered]@{schemaVersion='1.0';identity=[string]$task.taskId;attempt=[int]$task.attemptCount;baseSha=[string]$task.baseSha;startingHead=(Invoke-RalphNative git @('-C',[string]$task.worktree,'rev-parse','HEAD')).Output.Trim();createdAt=[DateTimeOffset]::UtcNow.ToString('O');item=$task};Write-RalphImmutableJson (Get-RalphAttemptPath $paths assignment ([string]$task.taskId) ([int]$task.attemptCount)) $assignment};Save-BuildLedger $tasks $paths
        $jobs=foreach($task in $wave){$taskJson=$task|ConvertTo-Json -Depth 50 -Compress;$resultPath=Get-RalphAttemptPath $paths result ([string]$task.taskId) ([int]$task.attemptCount);Start-ThreadJob -Name ([string]$task.taskId) -ArgumentList @($PSScriptRoot,$root,[string]$task.worktree,$taskJson,$resultPath) -ScriptBlock{param($ScriptsPath,$RepositoryRoot,$Worktree,$TaskJson,$ResultPath);.(Join-Path $ScriptsPath 'common.ps1');$task=$TaskJson|ConvertFrom-Json -Depth 50;$context='Task assignment:'+[Environment]::NewLine+($task|ConvertTo-Json -Depth 50);try{$result=Invoke-RalphRole $RepositoryRoot $Worktree 'builder' $context 'builder-result.schema.json' 'workspace-write';$record=[ordered]@{schemaVersion='1.0';identity=[string]$task.taskId;attempt=[int]$task.attemptCount;succeeded=$true;result=$result;error=$null;completedAt=[DateTimeOffset]::UtcNow.ToString('O')}}catch{$record=[ordered]@{schemaVersion='1.0';identity=[string]$task.taskId;attempt=[int]$task.attemptCount;succeeded=$false;result=$null;error=$_.Exception.Message;completedAt=[DateTimeOffset]::UtcNow.ToString('O')}};Write-RalphImmutableJson $ResultPath $record;$record}}
        $outer=([int]$configuration.agentTimeoutMinutes*60)+[int]$configuration.agentCleanupGraceSeconds+30;try{[void](Wait-Job $jobs -Timeout $outer);foreach($job in $jobs){$task=@($wave|Where-Object taskId -eq $job.Name)[0];if($job.State-ne'Completed'){Stop-Job $job -ErrorAction SilentlyContinue;$task.status='pending';$task.lastError='Builder did not stop after the bounded agent deadline.';continue};$record=Receive-Job $job;if($null-eq$record-or-not[bool]$record.succeeded){$task.status='pending';$task.lastError=if($null-eq$record){'Builder returned no durable result.'}else{[string]$record.error}}else{$task.status='result_ready'}}}finally{$jobs|ForEach-Object{if($_.State-ne'Completed'){Stop-Job $_ -ErrorAction SilentlyContinue};Remove-Job $_ -Force -ErrorAction SilentlyContinue}};Save-BuildLedger $tasks $paths;Show-RalphStatus $state $tasks
    }
    Write-Host 'BUILD: final drift check';Assert-RalphPlanDrift $state $root -RequirePlan;Assert-RalphLedgerIdentity $state $tasks task;[void](Assert-RalphTargetDrift $root $configuration $state);$state.integrationSha=Ensure-RalphIntegrationBranch $root $configuration $state (Get-KnownTaskMergeShas $tasks)
    $restartBuild=$false
    Write-Host 'BUILD: create integration verifier worktree'
    $verificationWorktree=New-RalphAuditWorktree $root $configuration "$($configuration.remote)/$($configuration.integrationBranch)"
    try {
        Write-Host 'BUILD: run integration verifier'
        $context="Perform lightweight integration verification at exact SHA $($state.integrationSha). Confirm the build and direct smoke checks only."
        $integrationVerification=Invoke-RalphRole $root $verificationWorktree 'verifier' $context 'verifier-result.schema.json' 'read-only'
        if(-not[bool]$integrationVerification.approved){
            $blocker=ConvertTo-RalphStructuredBlocker $integrationVerification.blocker 'build' $null
            if(Test-RalphSemanticBlocker $blocker){
                [void](Invoke-RalphPmResolution $root $configuration $state $paths $tasks $bugs 'build' 'verification' $null $blocker $PmInputReader)
                $restartBuild=$true
            } else {
                throw "Lightweight integration verification failed: $(@($integrationVerification.findings)-join'; ')"
            }
        }
    } finally {
        Write-Host 'BUILD: remove verifier worktree'
        Remove-RalphAuditWorktree $root $configuration
    }
    if($restartBuild){$lock.Dispose();$lock=$null;&$PSCommandPath -Repository $root -PmInputReader $PmInputReader;return}
    Assert-RalphPlanDrift $state $root -RequirePlan;Assert-RalphLedgerIdentity $state $tasks task;[void](Assert-RalphTargetDrift $root $configuration $state);$tasks.status='complete';Save-BuildLedger $tasks $paths;$state.stage='audit';$state.stageStatus='not_started';$state.blocker=$null;Save-RalphState $state $paths
    $activeTasks=@($tasks.tasks|Where-Object status -ne 'superseded');$attempts=@($activeTasks|ForEach-Object{[int]$_.attemptCount});$summary=[ordered]@{completedAt=[DateTimeOffset]::UtcNow.ToString('O');totalTasks=$activeTasks.Count;supersededTasks=@($tasks.tasks|Where-Object status -eq 'superseded').Count;integratedTasks=@($activeTasks|Where-Object status -eq 'integrated').Count;totalAttempts=($attempts|Measure-Object -Sum).Sum;averageAttempts=if($attempts.Count){[Math]::Round((($attempts|Measure-Object -Average).Average),2)}else{0};taskCommits=@($activeTasks.resultSha|Where-Object{$_});pullRequests=@($activeTasks.pullRequest|Where-Object{$_});integrationSha=$state.integrationSha;integrationVerification=$integrationVerification;remainingBlockers=@()};Write-RalphSummary $paths.BuildSummary $summary;Show-RalphStatus $state $tasks;Write-Host 'BUILD COMPLETE: all active tasks are integrated. The audit loop may run.'
}catch{if($null-ne$state){if($null-ne$tasks){try{Save-BuildLedger $tasks $paths}catch{}};Set-RalphBlocked $state $paths 'build' $null $_.Exception.Message 'Resolve the exact task, provider, drift, or environment blocker, then rerun build-loop.ps1.'};throw}finally{if($null-ne$lock){$lock.Dispose()}}
