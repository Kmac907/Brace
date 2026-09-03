param(
    [string]$Repository = (Get-Location).Path,
    [switch]$StartNewWorkflow
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$root = Get-RalphRepositoryRoot -Path $Repository
$configuration = Get-RalphConfiguration -RepositoryRoot $root
Assert-RalphPrerequisites -Configuration $configuration -RequireCodex
$paths = Initialize-RalphStateFiles -RepositoryRoot $root -Configuration $configuration
$lock = Enter-RalphWorkflowLock -Path $paths.Lock
$state = $null

try {
    $state = Read-RalphJson -Path $paths.State -SchemaPath (Join-Path $paths.Schemas 'state.schema.json')
    Assert-RalphStateIdentity -State $state -RepositoryRoot $root -Configuration $configuration
    if ($StartNewWorkflow) {
        Reset-RalphCompletedWorkflow -RepositoryRoot $root -Configuration $configuration -State $state
        $paths = Initialize-RalphStateFiles -RepositoryRoot $root -Configuration $configuration
        $state = Read-RalphJson -Path $paths.State -SchemaPath (Join-Path $paths.Schemas 'state.schema.json')
    }
    $tasks = Read-RalphJson -Path $paths.Tasks -SchemaPath (Join-Path $paths.Schemas 'tasks.schema.json')

    if ([string]$tasks.status -in @('active', 'complete') -or [string]$state.stage -in @('audit', 'complete') -or @($tasks.tasks | Where-Object { [int]$_.attemptCount -gt 0 }).Count -gt 0) {
        throw 'Planning cannot replace a task queue after implementation has begun.'
    }
    if ([string]$tasks.status -ceq 'ready' -and [string]$state.stage -ceq 'build') {
        Assert-RalphPlanDrift -State $state -RepositoryRoot $root -RequirePlan
        Assert-RalphLedgerIdentity -State $state -Ledger $tasks -Kind task
        [void](Assert-RalphTargetDrift -RepositoryRoot $root -Configuration $configuration -State $state)
        Show-RalphStatus -State $state -Tasks $tasks
        Write-Host 'Planning is already complete. The build loop may run.'
        return
    }

    $requirementsPath = Join-Path $root 'requirements.md'
    if (-not [System.IO.File]::Exists($requirementsPath)) {
        throw 'requirements.md does not exist.'
    }
    [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'fetch', [string]$configuration.remote, '--prune'))
    $currentBranch = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'branch', '--show-current')).Output.Trim()
    if ($currentBranch -cne [string]$configuration.targetBranch) {
        throw "Planning must run from the target branch $($configuration.targetBranch), not $currentBranch."
    }
    $localTargetSha = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'rev-parse', 'HEAD')).Output.Trim()
    $remoteTargetSha = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'rev-parse', "$($configuration.remote)/$($configuration.targetBranch)")).Output.Trim()
    if ($localTargetSha -cne $remoteTargetSha) { throw 'Local target branch must exactly match its remote before planning.' }
    $pendingPaths = @((Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'status', '--porcelain', '--untracked-files=all')).Lines | ForEach-Object {
        if ($_.Length -gt 3) { $_.Substring(3).Replace('\', '/') }
    })
    $unexpectedPaths = @($pendingPaths | Where-Object { $_ -notin @('requirements.md', 'plan.md') })
    if ($unexpectedPaths.Count -gt 0) {
        throw "Planning found unrelated uncommitted work: $($unexpectedPaths -join ', ')"
    }

    $state.stage = 'planning'
    $state.stageStatus = 'running'
    $state.blocker = $null
    Save-RalphState -State $state -Paths $paths

    $completedResult = $null
    for ($round = 1; $round -le [int]$configuration.maximumPlanningQuestionRounds; $round += 1) {
        $context = @"
Repository root: $root
Question round: $round of $($configuration.maximumPlanningQuestionRounds)

Inspect requirements.md and the existing repository directly. If confirmed clarifications are present, treat them as authoritative. If the requirements are sufficient, return the normalized requirements, complete plan, and complete task graph now.
"@
        $result = Invoke-RalphRole -RepositoryRoot $root -WorkingDirectory $root -Role 'planner' -Context $context -SchemaName 'planning-result.schema.json' -Sandbox 'read-only'
        if ([string]$result.status -ceq 'questions') {
            if (@($result.questions).Count -eq 0) { throw 'Planner requested clarification without returning questions.' }
            $answers = [System.Collections.Generic.List[string]]::new()
            foreach ($question in @($result.questions)) {
                Write-Host ''
                Write-Host "PLANNING QUESTION $($question.questionId)"
                Write-Host $question.question
                Write-Host "Why this is needed: $($question.reason)"
                $answer = Read-Host 'Answer'
                if ([string]::IsNullOrWhiteSpace($answer)) { throw "No answer was supplied for $($question.questionId)." }
                $answers.Add("### $($question.questionId)`n`nQuestion: $($question.question)`n`nAnswer: $answer")
            }
            $requirements = Read-RalphText -Path $requirementsPath
            $amendment = "`n`n" + ($answers -join "`n`n") + "`n"
            Write-RalphTextAtomic -Path $requirementsPath -Text ($requirements.TrimEnd() + $amendment)
            continue
        }

        if ([string]::IsNullOrWhiteSpace([string]$result.normalizedRequirementsMarkdown)) { throw 'Planner completed without normalized requirements.' }
        if ([string]::IsNullOrWhiteSpace([string]$result.planMarkdown)) { throw 'Planner completed without plan.md content.' }
        if (@($result.tasks).Count -eq 0) { throw 'Planner completed without implementation tasks.' }
        Assert-RalphGraph -Items @($result.tasks) -Kind task
        Assert-RalphTaskCoverage -Tasks @($result.tasks) -RequirementsMarkdown ([string]$result.normalizedRequirementsMarkdown) -DeferredRequirementIds @($result.summary.deferredRequirementIds)
        $completedResult = $result
        break
    }

    if ($null -eq $completedResult) {
        throw "Planning did not complete within $($configuration.maximumPlanningQuestionRounds) clarification rounds."
    }

    Write-RalphTextAtomic -Path $requirementsPath -Text ([string]$completedResult.normalizedRequirementsMarkdown).TrimEnd()
    $planPath = Join-Path $root 'plan.md'
    Write-RalphTextAtomic -Path $planPath -Text ([string]$completedResult.planMarkdown).TrimEnd()
    $planHash = Get-RalphFileHash -Path $planPath
    [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'add', '--', 'requirements.md', 'plan.md'))
    $hasPlanningChanges = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'diff', '--cached', '--quiet') -AllowedExitCodes @(0, 1)).ExitCode -eq 1
    if ($hasPlanningChanges) {
        [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'commit', '-m', 'Plan project implementation'))
    }
    [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'push', [string]$configuration.remote, [string]$configuration.targetBranch))
    [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'fetch', [string]$configuration.remote))
    $localPlanningSha = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'rev-parse', 'HEAD')).Output.Trim()
    $remotePlanningSha = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'rev-parse', "$($configuration.remote)/$($configuration.targetBranch)")).Output.Trim()
    if ($localPlanningSha -cne $remotePlanningSha) { throw 'Remote target branch does not match the committed planning result.' }

    $persistedTasks = foreach ($task in @($completedResult.tasks)) {
        [ordered]@{
            taskId = [string]$task.taskId
            title = [string]$task.title
            description = [string]$task.description
            status = 'pending'
            requirementIds = @($task.requirementIds)
            planSections = @($task.planSections)
            dependencies = @($task.dependencies)
            allowedPaths = @($task.allowedPaths)
            exclusiveResources = @($task.exclusiveResources)
            acceptanceCriteria = @($task.acceptanceCriteria)
            checks = @($task.checks)
            attemptCount = 0
            branch = $null
            worktree = $null
            baseSha = $null
            resultSha = $null
            pullRequest = $null
            lastError = $null
        }
    }
    $definitionHash = Get-RalphDefinitionHash -Items @($persistedTasks) -Kind task
    $tasks = [ordered]@{
        schemaVersion = '1.0'
        revision = [int]$tasks.revision + 1
        planHash = $planHash
        definitionHash = $definitionHash
        status = 'ready'
        tasks = @($persistedTasks)
    }
    Write-RalphJsonAtomic -Path $paths.Tasks -Value $tasks -SchemaPath (Join-Path $paths.Schemas 'tasks.schema.json')

    $state.stage = 'build'
    $state.stageStatus = 'not_started'
    $state.requirementsHash = Get-RalphFileHash -Path $requirementsPath
    $state.planHash = $planHash
    $state.targetBaseSha = $remotePlanningSha
    $state.taskDefinitionHash = $definitionHash
    $state.bugDefinitionHash = $null
    $state.blocker = $null
    Save-RalphState -State $state -Paths $paths

    $summary = [ordered]@{
        completedAt = [DateTimeOffset]::UtcNow.ToString('O')
        requirementsHash = $state.requirementsHash
        planHash = $planHash
        requirementsCount = [int]$completedResult.summary.requirementsCount
        taskCount = @($persistedTasks).Count
        parallelizableTaskCount = [int]$completedResult.summary.parallelizableTaskCount
        dependencyCount = @($persistedTasks | ForEach-Object { @($_.dependencies) }).Count
        assumptions = @($completedResult.summary.assumptions)
        deferredScope = @($completedResult.summary.deferredScope)
        deferredRequirementIds = @($completedResult.summary.deferredRequirementIds)
        filesCreatedOrUpdated = @('requirements.md', 'plan.md', '.codex/tasks.json', '.codex/state.json', '.codex/planning-summary.json')
    }
    Write-RalphSummary -Path $paths.PlanningSummary -Summary $summary
    Show-RalphStatus -State $state -Tasks $tasks
    Write-Host 'PLANNING COMPLETE: requirements, plan, and task queue are ready.'
}
catch {
    if ($null -ne $state) {
        Set-RalphBlocked -State $state -Paths $paths -Scope 'planning' -Message $_.Exception.Message -RequiredDecision 'Correct the reported requirement, configuration, or environment issue, then rerun planning-loop.ps1.'
    }
    throw
}
finally {
    if ($null -ne $lock) { $lock.Dispose() }
}
