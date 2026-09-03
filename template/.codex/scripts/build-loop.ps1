param(
    [string]$Repository = (Get-Location).Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

function Save-BuildLedger {
    param($Ledger, $Paths)
    $Ledger.revision = [int]$Ledger.revision + 1
    Write-RalphJsonAtomic -Path $Paths.Tasks -Value $Ledger -SchemaPath (Join-Path $Paths.Schemas 'tasks.schema.json')
}

$root = Get-RalphRepositoryRoot -Path $Repository
$configuration = Get-RalphConfiguration -RepositoryRoot $root
Assert-RalphPrerequisites -Configuration $configuration -RequireCodex
if (-not (Get-Command Start-ThreadJob -ErrorAction SilentlyContinue)) { throw 'PowerShell ThreadJob support is required for concurrent builders.' }
$paths = Initialize-RalphStateFiles -RepositoryRoot $root -Configuration $configuration
$lock = Enter-RalphWorkflowLock -Path $paths.Lock
$state = $null
$tasks = $null

try {
    $state = Read-RalphJson -Path $paths.State -SchemaPath (Join-Path $paths.Schemas 'state.schema.json')
    $tasks = Read-RalphJson -Path $paths.Tasks -SchemaPath (Join-Path $paths.Schemas 'tasks.schema.json')
    Assert-RalphStateIdentity -State $state -RepositoryRoot $root -Configuration $configuration
    Assert-RalphPlanDrift -State $state -RepositoryRoot $root -RequirePlan
    Assert-RalphGraph -Items @($tasks.tasks) -Kind task

    if ([string]$tasks.status -ceq 'complete' -and [string]$state.stage -eq 'audit') {
        Show-RalphStatus -State $state -Tasks $tasks
        Write-Host 'BUILD COMPLETE: the audit loop may run.'
        return
    }
    if ([string]$tasks.status -notin @('ready', 'active', 'blocked')) { throw 'Planning has not produced a buildable task queue.' }
    if ([string]$state.stage -notin @('build', 'blocked')) { throw "The workflow is at stage $($state.stage), not build." }

    $state.stage = 'build'
    $state.stageStatus = 'running'
    $state.blocker = $null
    $tasks.status = 'active'
    Save-RalphState -State $state -Paths $paths

    $integrationSha = Ensure-RalphIntegrationBranch -RepositoryRoot $root -Configuration $configuration -State $state
    $state.integrationSha = $integrationSha
    Save-RalphState -State $state -Paths $paths

    foreach ($task in @($tasks.tasks | Where-Object status -eq 'submitted')) {
        $existing = Get-RalphPullRequest -RepositoryRoot $root -Configuration $configuration -Head ([string]$task.branch) -Base ([string]$configuration.integrationBranch)
        if ($null -ne $existing -and [string]$existing.state -in @('merged', 'completed')) {
            $task.pullRequest = $existing
            $task.status = 'integrated'
            $state.integrationSha = [string]$existing.mergeSha
            Save-BuildLedger -Ledger $tasks -Paths $paths
            try { Remove-RalphMergedAssignment -RepositoryRoot $root -Configuration $configuration -Identity ([string]$task.taskId) -Branch ([string]$task.branch) -PullRequest $existing } catch { Write-Warning $_.Exception.Message }
        } elseif ($null -ne $existing -and [string]$existing.state -in @('open', 'active')) {
            $merged = Complete-RalphPullRequest -RepositoryRoot $root -Configuration $configuration -PullRequest $existing
            $task.pullRequest = $merged
            $task.status = 'integrated'
            $state.integrationSha = [string]$merged.mergeSha
            Save-BuildLedger -Ledger $tasks -Paths $paths
            try { Remove-RalphMergedAssignment -RepositoryRoot $root -Configuration $configuration -Identity ([string]$task.taskId) -Branch ([string]$task.branch) -PullRequest $merged } catch { Write-Warning $_.Exception.Message }
        } else {
            $task.status = 'pending'
            $task.lastError = 'Submitted task had no reusable pull request; assignment will be reconciled.'
        }
    }
    foreach ($task in @($tasks.tasks | Where-Object status -eq 'active')) {
        $task.status = 'pending'
        $task.lastError = 'Resuming an interrupted assignment from its recorded worktree and branch.'
    }
    Save-BuildLedger -Ledger $tasks -Paths $paths

    while (@($tasks.tasks | Where-Object status -ne 'integrated').Count -gt 0) {
        Assert-RalphPlanDrift -State $state -RepositoryRoot $root -RequirePlan
        $exhausted = @($tasks.tasks | Where-Object { $_.status -ne 'integrated' -and [int]$_.attemptCount -ge [int]$configuration.maximumTaskAttempts })
        if ($exhausted.Count -gt 0) {
            foreach ($task in $exhausted) { $task.status = 'blocked' }
            $tasks.status = 'blocked'
            Save-BuildLedger -Ledger $tasks -Paths $paths
            throw "Task attempts exhausted: $(@($exhausted.taskId) -join ', ')"
        }

        $wave = @(Select-RalphReadyItems -Items @($tasks.tasks) -Kind task -Maximum ([int]$configuration.maximumConcurrentBuilders))
        if ($wave.Count -eq 0) { throw 'No dependency-ready, non-conflicting tasks remain. Inspect blocked dependencies and path ownership.' }

        $baseSha = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'rev-parse', [string]$configuration.integrationBranch)).Output.Trim()
        foreach ($task in $wave) {
            $task.branch = "worktree/$($task.taskId)"
            $task.baseSha = $baseSha
            $task.worktree = New-RalphWorktree -RepositoryRoot $root -Configuration $configuration -Identity ([string]$task.taskId) -Branch ([string]$task.branch) -BaseReference $baseSha
            $task.status = 'active'
            $task.attemptCount = [int]$task.attemptCount + 1
            $task.lastError = $null
        }
        Save-BuildLedger -Ledger $tasks -Paths $paths

        $jobs = foreach ($task in $wave) {
            $taskJson = $task | ConvertTo-Json -Depth 50 -Compress
            Start-ThreadJob -Name ([string]$task.taskId) -ArgumentList @($PSScriptRoot, $root, [string]$task.worktree, $taskJson) -ScriptBlock {
                param($ScriptsPath, $RepositoryRoot, $Worktree, $TaskJson)
                . (Join-Path $ScriptsPath 'common.ps1')
                $task = $TaskJson | ConvertFrom-Json -Depth 50
                $context = 'Task assignment:' + [Environment]::NewLine + ($task | ConvertTo-Json -Depth 50)
                try {
                    $result = Invoke-RalphRole -RepositoryRoot $RepositoryRoot -WorkingDirectory $Worktree -Role 'builder' -Context $context -SchemaName 'builder-result.schema.json' -Sandbox 'workspace-write'
                    [pscustomobject]@{ identity = [string]$task.taskId; succeeded = $true; result = $result; error = $null }
                } catch {
                    [pscustomobject]@{ identity = [string]$task.taskId; succeeded = $false; result = $null; error = $_.Exception.Message }
                }
            }
        }

        [void](Wait-Job -Job $jobs -Timeout 5400)
        foreach ($job in $jobs) {
            $task = @($wave | Where-Object taskId -eq $job.Name)[0]
            if ($job.State -ne 'Completed') {
                Stop-Job -Job $job -ErrorAction SilentlyContinue
                $task.status = 'pending'
                $task.lastError = 'Builder exceeded the 90-minute assignment deadline.'
                continue
            }
            $jobResult = Receive-Job -Job $job
            if ($null -eq $jobResult -or -not [bool]$jobResult.succeeded) {
                $task.status = 'pending'
                $task.lastError = if ($null -eq $jobResult) { 'Builder returned no result.' } else { [string]$jobResult.error }
                continue
            }
            $result = $jobResult.result
            if ([string]$result.status -ceq 'blocked') {
                $task.status = 'pending'
                $task.lastError = [string]$result.blocker
                continue
            }

            try {
                $commit = Assert-RalphAssignmentCommit -Worktree ([string]$task.worktree) -BaseSha ([string]$task.baseSha) -Item $task
                if ([string]$result.commitSha -cne [string]$commit.Head) { throw 'Builder result commit SHA does not match the worktree HEAD.' }
                $task.resultSha = [string]$commit.Head
                $verificationContext = 'Verify only this task:' + [Environment]::NewLine + ($task | ConvertTo-Json -Depth 50) + [Environment]::NewLine + 'Builder result:' + [Environment]::NewLine + ($result | ConvertTo-Json -Depth 50)
                $verification = Invoke-RalphRole -RepositoryRoot $root -WorkingDirectory ([string]$task.worktree) -Role 'verifier' -Context $verificationContext -SchemaName 'verifier-result.schema.json' -Sandbox 'read-only'
                if (-not [bool]$verification.approved) { throw "Focused verification failed: $(@($verification.findings) -join '; ')" }
                $task.status = 'submitted'
                Save-BuildLedger -Ledger $tasks -Paths $paths
                $merged = Publish-RalphAssignment -RepositoryRoot $root -Worktree ([string]$task.worktree) -Configuration $configuration -Item $task -Kind task
                $task.pullRequest = $merged
                $task.status = 'integrated'
                $task.lastError = $null
                $state.integrationSha = [string]$merged.mergeSha
                [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'branch', '-f', [string]$configuration.integrationBranch, "$($configuration.remote)/$($configuration.integrationBranch)"))
                Save-BuildLedger -Ledger $tasks -Paths $paths
                Save-RalphState -State $state -Paths $paths
                try { Remove-RalphMergedAssignment -RepositoryRoot $root -Configuration $configuration -Identity ([string]$task.taskId) -Branch ([string]$task.branch) -PullRequest $merged } catch { Write-Warning $_.Exception.Message }
            } catch {
                $task.status = 'pending'
                $task.lastError = $_.Exception.Message
                Write-Warning "$($task.taskId) attempt $($task.attemptCount) failed: $($task.lastError) $($_.ScriptStackTrace)"
            }
        }
        $jobs | Remove-Job -Force -ErrorAction SilentlyContinue
        Save-BuildLedger -Ledger $tasks -Paths $paths
        Show-RalphStatus -State $state -Tasks $tasks
    }

    $verificationWorktree = New-RalphAuditWorktree -RepositoryRoot $root -Configuration $configuration -Reference "$($configuration.remote)/$($configuration.integrationBranch)"
    try {
        $integrationContext = "Perform only a lightweight integration verification of completed tasks at integration SHA $($state.integrationSha). Confirm the project builds and the most direct smoke checks pass. Do not perform the deep audit."
        $integrationVerification = Invoke-RalphRole -RepositoryRoot $root -WorkingDirectory $verificationWorktree -Role 'verifier' -Context $integrationContext -SchemaName 'verifier-result.schema.json' -Sandbox 'read-only'
        if (-not [bool]$integrationVerification.approved) { throw "Lightweight integration verification failed: $(@($integrationVerification.findings) -join '; ')" }
    } finally {
        Remove-RalphAuditWorktree -RepositoryRoot $root -Configuration $configuration
    }

    $tasks.status = 'complete'
    Save-BuildLedger -Ledger $tasks -Paths $paths
    $state.stage = 'audit'
    $state.stageStatus = 'not_started'
    $state.blocker = $null
    Save-RalphState -State $state -Paths $paths
    $attempts = @($tasks.tasks | ForEach-Object { [int]$_.attemptCount })
    $summary = [ordered]@{
        completedAt = [DateTimeOffset]::UtcNow.ToString('O')
        totalTasks = @($tasks.tasks).Count
        integratedTasks = @($tasks.tasks | Where-Object status -eq 'integrated').Count
        totalAttempts = ($attempts | Measure-Object -Sum).Sum
        averageAttempts = if ($attempts.Count -eq 0) { 0 } else { [Math]::Round((($attempts | Measure-Object -Average).Average), 2) }
        taskCommits = @($tasks.tasks.resultSha | Where-Object { $null -ne $_ })
        pullRequests = @($tasks.tasks.pullRequest | Where-Object { $null -ne $_ })
        integrationSha = $state.integrationSha
        integrationVerification = $integrationVerification
        remainingBlockers = @()
    }
    Write-RalphSummary -Path $paths.BuildSummary -Summary $summary
    Show-RalphStatus -State $state -Tasks $tasks
    Write-Host 'BUILD COMPLETE: all tasks are integrated. The audit loop may run.'
}
catch {
    if ($null -ne $state) {
        if ($null -ne $tasks) { try { Save-BuildLedger -Ledger $tasks -Paths $paths } catch {} }
        Set-RalphBlocked -State $state -Paths $paths -Scope 'build' -Message $_.Exception.Message -RequiredDecision 'Resolve the reported task, provider, drift, or environment blocker, then rerun build-loop.ps1.'
    }
    throw
}
finally {
    if ($null -ne $lock) { $lock.Dispose() }
}
