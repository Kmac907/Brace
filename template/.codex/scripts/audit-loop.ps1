param(
    [string]$Repository = (Get-Location).Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

function Save-BugLedger {
    param($Ledger, $Paths)
    $Ledger.revision = [int]$Ledger.revision + 1
    Write-RalphJsonAtomic -Path $Paths.Bugs -Value $Ledger -SchemaPath (Join-Path $Paths.Schemas 'bugs.schema.json')
}

$root = Get-RalphRepositoryRoot -Path $Repository
$configuration = Get-RalphConfiguration -RepositoryRoot $root
Assert-RalphPrerequisites -Configuration $configuration -RequireCodex
if (-not (Get-Command Start-ThreadJob -ErrorAction SilentlyContinue)) { throw 'PowerShell ThreadJob support is required for concurrent bug fixers.' }
$paths = Initialize-RalphStateFiles -RepositoryRoot $root -Configuration $configuration
$lock = Enter-RalphWorkflowLock -Path $paths.Lock
$state = $null
$bugs = $null
$auditWorktree = $null

try {
    $state = Read-RalphJson -Path $paths.State -SchemaPath (Join-Path $paths.Schemas 'state.schema.json')
    $tasks = Read-RalphJson -Path $paths.Tasks -SchemaPath (Join-Path $paths.Schemas 'tasks.schema.json')
    $bugs = Read-RalphJson -Path $paths.Bugs -SchemaPath (Join-Path $paths.Schemas 'bugs.schema.json')
    Assert-RalphStateIdentity -State $state -RepositoryRoot $root -Configuration $configuration
    Assert-RalphPlanDrift -State $state -RepositoryRoot $root -RequirePlan
    if ([string]$tasks.status -cne 'complete' -or @($tasks.tasks | Where-Object status -ne 'integrated').Count -gt 0) {
        throw 'Every implementation task must be integrated before audit begins.'
    }
    if ([string]$state.stage -ceq 'complete') {
        Show-RalphStatus -State $state -Tasks $tasks -Bugs $bugs
        Write-Host 'PROJECT COMPLETE: audit, bug fixes, validation, and final merge are finished.'
        return
    }
    if ([string]$state.stage -notin @('audit', 'blocked')) { throw "The workflow is at stage $($state.stage), not audit." }

    $state.stage = 'audit'
    $state.stageStatus = 'running'
    $state.blocker = $null
    $integrationSha = Ensure-RalphIntegrationBranch -RepositoryRoot $root -Configuration $configuration -State $state
    $state.integrationSha = $integrationSha
    Save-RalphState -State $state -Paths $paths

    if ([string]$bugs.status -ceq 'not_audited') {
        $auditWorktree = New-RalphAuditWorktree -RepositoryRoot $root -Configuration $configuration -Reference "$($configuration.remote)/$($configuration.integrationBranch)"
        $context = "Audit the complete implementation at exact integration commit $integrationSha. Return the complete bounded finding set in one response. Do not edit the worktree."
        $auditResult = Invoke-RalphRole -RepositoryRoot $root -WorkingDirectory $auditWorktree -Role 'auditor' -Context $context -SchemaName 'audit-result.schema.json' -Sandbox 'read-only'
        if (@($auditResult.bugs).Count -gt 0) { Assert-RalphGraph -Items @($auditResult.bugs) -Kind bug }
        $persistedBugs = foreach ($bug in @($auditResult.bugs)) {
            [ordered]@{
                bugId = [string]$bug.bugId
                title = [string]$bug.title
                severity = [string]$bug.severity
                category = [string]$bug.category
                status = 'open'
                requirementIds = @($bug.requirementIds)
                description = [string]$bug.description
                evidence = [string]$bug.evidence
                actualBehavior = [string]$bug.actualBehavior
                requiredBehavior = [string]$bug.requiredBehavior
                impact = [string]$bug.impact
                requiredCorrection = [string]$bug.requiredCorrection
                acceptanceTest = [string]$bug.acceptanceTest
                dependencies = @($bug.dependencies)
                allowedPaths = @($bug.allowedPaths)
                exclusiveResources = @($bug.exclusiveResources)
                attemptCount = 0
                branch = $null
                worktree = $null
                baseSha = $null
                resultSha = $null
                pullRequest = $null
                lastError = $null
            }
        }
        $bugs = [ordered]@{ schemaVersion = '1.0'; revision = [int]$bugs.revision + 1; auditSha = $integrationSha; status = 'ready'; bugs = @($persistedBugs) }
        Write-RalphJsonAtomic -Path $paths.Bugs -Value $bugs -SchemaPath (Join-Path $paths.Schemas 'bugs.schema.json')
        Remove-RalphAuditWorktree -RepositoryRoot $root -Configuration $configuration
        $auditWorktree = $null
    } else {
        if ([string]::IsNullOrWhiteSpace([string]$bugs.auditSha)) { throw 'Existing bug ledger has no valid audit SHA.' }
        $auditAncestor = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'merge-base', '--is-ancestor', [string]$bugs.auditSha, $integrationSha) -AllowedExitCodes @(0, 1)).ExitCode -eq 0
        if (-not $auditAncestor) { throw 'Current integration branch does not descend from the frozen audit SHA.' }
        if (@($bugs.bugs).Count -gt 0) { Assert-RalphGraph -Items @($bugs.bugs) -Kind bug }
    }

    foreach ($bug in @($bugs.bugs | Where-Object status -eq 'fixed')) {
        if ($null -eq $bug.pullRequest) {
            $bug.status = 'verified'
            continue
        }
        $existing = Get-RalphPullRequest -RepositoryRoot $root -Configuration $configuration -Head ([string]$bug.branch) -Base ([string]$configuration.integrationBranch)
        if ($null -eq $existing) {
            $bug.status = 'open'
            $bug.lastError = 'Fixed bug had no reusable pull request; assignment will be reconciled.'
            continue
        }
        $merged = if ([string]$existing.state -in @('merged', 'completed')) { $existing } else { Complete-RalphPullRequest -RepositoryRoot $root -Configuration $configuration -PullRequest $existing }
        $bug.pullRequest = $merged
        $bug.status = 'verified'
        $state.integrationSha = [string]$merged.mergeSha
        Save-BugLedger -Ledger $bugs -Paths $paths
        try { Remove-RalphMergedAssignment -RepositoryRoot $root -Configuration $configuration -Identity ([string]$bug.bugId) -Branch ([string]$bug.branch) -PullRequest $merged } catch { Write-Warning $_.Exception.Message }
    }
    foreach ($bug in @($bugs.bugs | Where-Object status -eq 'active')) {
        $bug.status = 'open'
        $bug.lastError = 'Resuming an interrupted bug assignment from its recorded worktree and branch.'
    }
    Save-BugLedger -Ledger $bugs -Paths $paths

    while (@($bugs.bugs | Where-Object status -ne 'verified').Count -gt 0) {
        Assert-RalphPlanDrift -State $state -RepositoryRoot $root -RequirePlan
        $exhausted = @($bugs.bugs | Where-Object { $_.status -ne 'verified' -and [int]$_.attemptCount -ge [int]$configuration.maximumBugAttempts })
        if ($exhausted.Count -gt 0) {
            foreach ($bug in $exhausted) { $bug.status = 'blocked' }
            $bugs.status = 'blocked'
            Save-BugLedger -Ledger $bugs -Paths $paths
            throw "Bug attempts exhausted: $(@($exhausted.bugId) -join ', ')"
        }
        $wave = @(Select-RalphReadyItems -Items @($bugs.bugs) -Kind bug -Maximum ([int]$configuration.maximumConcurrentFixers))
        if ($wave.Count -eq 0) { throw 'No dependency-ready, non-conflicting bugs remain. Inspect blocked dependencies and path ownership.' }
        $baseSha = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'rev-parse', "$($configuration.remote)/$($configuration.integrationBranch)")).Output.Trim()
        foreach ($bug in $wave) {
            $bug.branch = "worktree/$($bug.bugId)"
            $bug.baseSha = $baseSha
            $bug.worktree = New-RalphWorktree -RepositoryRoot $root -Configuration $configuration -Identity ([string]$bug.bugId) -Branch ([string]$bug.branch) -BaseReference $baseSha
            $bug.status = 'active'
            $bug.attemptCount = [int]$bug.attemptCount + 1
            $bug.lastError = $null
        }
        $bugs.status = 'active'
        Save-BugLedger -Ledger $bugs -Paths $paths

        $jobs = foreach ($bug in $wave) {
            $bugJson = $bug | ConvertTo-Json -Depth 50 -Compress
            Start-ThreadJob -Name ([string]$bug.bugId) -ArgumentList @($PSScriptRoot, $root, [string]$bug.worktree, $bugJson) -ScriptBlock {
                param($ScriptsPath, $RepositoryRoot, $Worktree, $BugJson)
                . (Join-Path $ScriptsPath 'common.ps1')
                $bug = $BugJson | ConvertFrom-Json -Depth 50
                $context = 'Bug assignment:' + [Environment]::NewLine + ($bug | ConvertTo-Json -Depth 50)
                try {
                    $result = Invoke-RalphRole -RepositoryRoot $RepositoryRoot -WorkingDirectory $Worktree -Role 'bug-fixer' -Context $context -SchemaName 'fixer-result.schema.json' -Sandbox 'workspace-write'
                    [pscustomobject]@{ identity = [string]$bug.bugId; succeeded = $true; result = $result; error = $null }
                } catch {
                    [pscustomobject]@{ identity = [string]$bug.bugId; succeeded = $false; result = $null; error = $_.Exception.Message }
                }
            }
        }
        [void](Wait-Job -Job $jobs -Timeout 5400)
        foreach ($job in $jobs) {
            $bug = @($wave | Where-Object bugId -eq $job.Name)[0]
            if ($job.State -ne 'Completed') {
                Stop-Job -Job $job -ErrorAction SilentlyContinue
                $bug.status = 'open'
                $bug.lastError = 'Bug fixer exceeded the 90-minute assignment deadline.'
                continue
            }
            $jobResult = Receive-Job -Job $job
            if ($null -eq $jobResult -or -not [bool]$jobResult.succeeded) {
                $bug.status = 'open'
                $bug.lastError = if ($null -eq $jobResult) { 'Bug fixer returned no result.' } else { [string]$jobResult.error }
                continue
            }
            $result = $jobResult.result
            if ([string]$result.status -ceq 'blocked') {
                $bug.status = 'open'
                $bug.lastError = [string]$result.blocker
                continue
            }
            try {
                if ([string]$result.status -ceq 'not_reproducible') {
                    $context = 'Verify only whether this bug is not reproducible:' + [Environment]::NewLine + ($bug | ConvertTo-Json -Depth 50) + [Environment]::NewLine + ($result | ConvertTo-Json -Depth 50)
                    $verification = Invoke-RalphRole -RepositoryRoot $root -WorkingDirectory ([string]$bug.worktree) -Role 'verifier' -Context $context -SchemaName 'verifier-result.schema.json' -Sandbox 'read-only'
                    if (-not [bool]$verification.approved) { throw "Not-reproducible disposition was rejected: $(@($verification.findings) -join '; ')" }
                    $bug.status = 'verified'
                    $bug.lastError = 'Independently verified as not reproducible.'
                    Remove-RalphWorktree -RepositoryRoot $root -Configuration $configuration -Identity ([string]$bug.bugId) -Branch ([string]$bug.branch)
                    continue
                }
                $commit = Assert-RalphAssignmentCommit -Worktree ([string]$bug.worktree) -BaseSha ([string]$bug.baseSha) -Item $bug
                if ([string]$result.commitSha -cne [string]$commit.Head) { throw 'Fixer result commit SHA does not match the worktree HEAD.' }
                $bug.resultSha = [string]$commit.Head
                $verificationContext = 'Verify only this bug correction:' + [Environment]::NewLine + ($bug | ConvertTo-Json -Depth 50) + [Environment]::NewLine + 'Fixer result:' + [Environment]::NewLine + ($result | ConvertTo-Json -Depth 50)
                $verification = Invoke-RalphRole -RepositoryRoot $root -WorkingDirectory ([string]$bug.worktree) -Role 'verifier' -Context $verificationContext -SchemaName 'verifier-result.schema.json' -Sandbox 'read-only'
                if (-not [bool]$verification.approved) { throw "Bug verification failed: $(@($verification.findings) -join '; ')" }
                $bug.status = 'fixed'
                Save-BugLedger -Ledger $bugs -Paths $paths
                $merged = Publish-RalphAssignment -RepositoryRoot $root -Worktree ([string]$bug.worktree) -Configuration $configuration -Item $bug -Kind bug
                $bug.pullRequest = $merged
                $bug.status = 'verified'
                $bug.lastError = $null
                $state.integrationSha = [string]$merged.mergeSha
                [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'branch', '-f', [string]$configuration.integrationBranch, "$($configuration.remote)/$($configuration.integrationBranch)"))
                Save-BugLedger -Ledger $bugs -Paths $paths
                Save-RalphState -State $state -Paths $paths
                try { Remove-RalphMergedAssignment -RepositoryRoot $root -Configuration $configuration -Identity ([string]$bug.bugId) -Branch ([string]$bug.branch) -PullRequest $merged } catch { Write-Warning $_.Exception.Message }
            } catch {
                $bug.status = 'open'
                $bug.lastError = $_.Exception.Message
            }
        }
        $jobs | Remove-Job -Force -ErrorAction SilentlyContinue
        Save-BugLedger -Ledger $bugs -Paths $paths
        Show-RalphStatus -State $state -Tasks $tasks -Bugs $bugs
    }

    $bugs.status = 'complete'
    Save-BugLedger -Ledger $bugs -Paths $paths
    [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'fetch', [string]$configuration.remote, '--prune'))
    $auditWorktree = New-RalphAuditWorktree -RepositoryRoot $root -Configuration $configuration -Reference "$($configuration.remote)/$($configuration.integrationBranch)"
    $finalContext = "Run final project validation at exact integration SHA $($state.integrationSha). Execute the project-wide build and test commands defined by plan.md. This is validation of the frozen ledger, not a new exploratory audit."
    $finalValidation = Invoke-RalphRole -RepositoryRoot $root -WorkingDirectory $auditWorktree -Role 'verifier' -Context $finalContext -SchemaName 'verifier-result.schema.json' -Sandbox 'read-only'
    if (-not [bool]$finalValidation.approved) { throw "Final validation failed: $(@($finalValidation.findings) -join '; ')" }
    Remove-RalphAuditWorktree -RepositoryRoot $root -Configuration $configuration
    $auditWorktree = $null

    $projectBody = "Completed Worktree Ralph project implementation and verified $(@($bugs.bugs).Count) audited bugs at integration SHA $($state.integrationSha)."
    $projectPr = New-RalphPullRequest -RepositoryRoot $root -Configuration $configuration -Head ([string]$configuration.integrationBranch) -Base ([string]$configuration.targetBranch) -Title 'Complete project implementation' -Body $projectBody
    $projectPr = Complete-RalphPullRequest -RepositoryRoot $root -Configuration $configuration -PullRequest $projectPr
    [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'fetch', [string]$configuration.remote, '--prune'))
    $finalSha = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'rev-parse', "$($configuration.remote)/$($configuration.targetBranch)")).Output.Trim()
    $currentBranch = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'branch', '--show-current')).Output.Trim()
    if ($currentBranch -ceq [string]$configuration.targetBranch) {
        [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'merge', '--ff-only', "$($configuration.remote)/$($configuration.targetBranch)"))
    } else {
        [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'branch', '-f', [string]$configuration.targetBranch, $finalSha))
    }
    $integrationLocal = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'show-ref', '--verify', '--quiet', "refs/heads/$($configuration.integrationBranch)") -AllowedExitCodes @(0, 1)).ExitCode -eq 0
    if ($integrationLocal) { [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'branch', '-D', '--', [string]$configuration.integrationBranch)) }
    $integrationRemote = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'show-ref', '--verify', '--quiet', "refs/remotes/$($configuration.remote)/$($configuration.integrationBranch)") -AllowedExitCodes @(0, 1)).ExitCode -eq 0
    if ($integrationRemote -and [bool]$configuration.deleteMergedBranches) { [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $root, 'push', [string]$configuration.remote, '--delete', [string]$configuration.integrationBranch)) }

    $state.stage = 'complete'
    $state.stageStatus = 'complete'
    $state.finalMergeSha = $finalSha

    $state.blocker = $null
    Save-RalphState -State $state -Paths $paths
    $attempts = @($bugs.bugs | ForEach-Object { [int]$_.attemptCount })
    $summary = [ordered]@{
        completedAt = [DateTimeOffset]::UtcNow.ToString('O')
        auditSha = $bugs.auditSha
        totalBugs = @($bugs.bugs).Count
        bugsBySeverity = @($bugs.bugs | Group-Object severity | ForEach-Object { [ordered]@{ severity = $_.Name; count = $_.Count } })
        verifiedBugs = @($bugs.bugs | Where-Object status -eq 'verified').Count
        totalAttempts = ($attempts | Measure-Object -Sum).Sum
        averageAttempts = if ($attempts.Count -eq 0) { 0 } else { [Math]::Round((($attempts | Measure-Object -Average).Average), 2) }
        bugCommits = @($bugs.bugs.resultSha | Where-Object { $null -ne $_ })
        bugPullRequests = @($bugs.bugs.pullRequest | Where-Object { $null -ne $_ })
        finalValidation = $finalValidation
        projectPullRequest = $projectPr
        finalMergeSha = $finalSha
        remainingLimitations = @()
    }
    Write-RalphSummary -Path $paths.AuditSummary -Summary $summary
    Show-RalphStatus -State $state -Tasks $tasks -Bugs $bugs
    Write-Host 'PROJECT COMPLETE: audit, bug fixes, final validation, and merge to main succeeded.'
}
catch {
    if ($null -ne $auditWorktree) { try { Remove-RalphAuditWorktree -RepositoryRoot $root -Configuration $configuration } catch {} }
    if ($null -ne $state) {
        if ($null -ne $bugs) { try { Save-BugLedger -Ledger $bugs -Paths $paths } catch {} }
        Set-RalphBlocked -State $state -Paths $paths -Scope 'audit' -Message $_.Exception.Message -RequiredDecision 'Resolve the reported bug, provider, validation, drift, or environment blocker, then rerun audit-loop.ps1.'
    }
    throw
}
finally {
    if ($null -ne $lock) { $lock.Dispose() }
}
