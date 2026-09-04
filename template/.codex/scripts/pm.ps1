Set-StrictMode -Version Latest

function Add-RalphPropertyIfMissing {
    param([Parameter(Mandatory)]$Object, [Parameter(Mandatory)][string]$Name, $Value)
    if ($null -eq $Object.PSObject.Properties[$Name]) {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

function Get-RalphGitBlobIdentity {
    param([Parameter(Mandatory)][string]$RepositoryRoot, [Parameter(Mandatory)][string]$Reference, [Parameter(Mandatory)][string]$Path)
    $oid = (Invoke-RalphNative git @('-C', $RepositoryRoot, 'rev-parse', "${Reference}:$Path")).Output.Trim()
    if ($oid -notmatch '^[0-9a-f]{40,64}$') { throw "Unable to identify $Path at $Reference." }
    "gitblob:$oid"
}

function Read-RalphGitText {
    param([Parameter(Mandatory)][string]$RepositoryRoot, [Parameter(Mandatory)][string]$Reference, [Parameter(Mandatory)][string]$Path)
    (Invoke-RalphNative git @('-C', $RepositoryRoot, 'show', "${Reference}:$Path")).Output
}

function New-RalphState {
    param([Parameter(Mandatory)][string]$RepositoryRoot, [Parameter(Mandatory)]$Configuration, [Parameter(Mandatory)][string]$RepositoryIdentity)
    $now = [DateTimeOffset]::UtcNow.ToString('O')
    $paths = Get-RalphPaths $RepositoryRoot
    $remoteUrl = (Invoke-RalphNative git @('-C', $RepositoryRoot, 'config', '--get', "remote.$($Configuration.remote).url")).Output.Trim()
    [ordered]@{
        schemaVersion = '1.2'; revision = 0; repositoryRoot = [IO.Path]::GetFullPath($RepositoryRoot)
        provider = [string]$Configuration.provider; repository = $RepositoryIdentity; remote = [string]$Configuration.remote
        remoteUrl = $remoteUrl; targetBranch = [string]$Configuration.targetBranch; targetBaseSha = $null
        integrationBranch = [string]$Configuration.integrationBranch; configurationHash = Get-RalphFileHash $paths.Config
        taskDefinitionHash = $null; bugDefinitionHash = $null; stage = 'requirements'; stageStatus = 'not_started'
        requirementsHash = $null; planHash = $null; integrationSha = $null; acceptedIntegrationShas = @()
        finalMergeSha = $null; blocker = $null; amendmentSequence = 0; activeAmendment = $null
        createdAt = $now; updatedAt = $now
    }
}

function Update-RalphWorkflowStateSchema {
    param([Parameter(Mandatory)][string]$RepositoryRoot, [Parameter(Mandatory)]$Paths)
    if ([IO.File]::Exists($Paths.State)) {
        $state = Read-RalphJson $Paths.State
        Add-RalphPropertyIfMissing $state amendmentSequence 0
        Add-RalphPropertyIfMissing $state activeAmendment $null
        Add-RalphPropertyIfMissing $state acceptedIntegrationShas @()
        if ($state.activeAmendment) {
            $amendment = $state.activeAmendment
            Add-RalphPropertyIfMissing $amendment decisionIdentity $null
            Add-RalphPropertyIfMissing $amendment authorizedDocumentationPaths @('requirements.md', 'plan.md')
            $identity = if ([string]$amendment.sourceIdentity -match '^(TASK|BUG)-[0-9]{4}$') { [string]$amendment.sourceIdentity } else { $null }
            Add-RalphPropertyIfMissing $amendment.blocker affectedIdentity $identity
            Add-RalphPropertyIfMissing $amendment.blocker smallestResolution ([string]$amendment.blocker.message)
            Add-RalphPropertyIfMissing $amendment.blocker prohibitedDecisions @()
            if ([string]::IsNullOrWhiteSpace([string]$amendment.blocker.evidence)) { $amendment.blocker.evidence = [string]$amendment.blocker.message }
        }
        if ([string]$state.schemaVersion -in @('1.0', '1.1')) {
            if ([string]$state.schemaVersion -ceq '1.0' -and $state.targetBaseSha) {
                $reference = if ($state.integrationSha) { [string]$state.integrationSha } else { [string]$state.targetBaseSha }
                try { $state.requirementsHash = Get-RalphGitBlobIdentity $RepositoryRoot $reference 'requirements.md' } catch { }
                try { $state.planHash = Get-RalphGitBlobIdentity $RepositoryRoot $reference 'plan.md' } catch { }
            }
            $state.schemaVersion = '1.2'
            Write-RalphJsonAtomic $Paths.State $state (Join-Path $Paths.Schemas 'state.schema.json')
        }
    }
    if ([IO.File]::Exists($Paths.Tasks)) {
        $tasks = Read-RalphJson $Paths.Tasks
        if ([string]$tasks.schemaVersion -ceq '1.0') {
            foreach ($task in @($tasks.tasks)) {
                Add-RalphPropertyIfMissing $task amendmentId $null
                Add-RalphPropertyIfMissing $task supersededBy @()
            }
            $tasks.schemaVersion = '1.1'
            if ([IO.File]::Exists($Paths.State)) {
                $state = Read-RalphJson $Paths.State
                if ($state.planHash) { $tasks.planHash = [string]$state.planHash }
            }
            Write-RalphJsonAtomic $Paths.Tasks $tasks (Join-Path $Paths.Schemas 'tasks.schema.json')
        }
    }
    if ([IO.File]::Exists($Paths.Bugs)) {
        $bugs = Read-RalphJson $Paths.Bugs
        if ([string]$bugs.schemaVersion -in @('1.0','1.1')) {
            foreach ($bug in @($bugs.bugs)) { Add-RalphPropertyIfMissing $bug amendmentId $null; Add-RalphPropertyIfMissing $bug dispositionEvidence $null }
            $bugs.schemaVersion = '1.2'
            Write-RalphJsonAtomic $Paths.Bugs $bugs (Join-Path $Paths.Schemas 'bugs.schema.json')
        }
    }
}

function Initialize-RalphStateFiles {
    param([Parameter(Mandatory)][string]$RepositoryRoot, [Parameter(Mandatory)]$Configuration)
    $paths = Get-RalphPaths $RepositoryRoot
    foreach ($directory in @($paths.Logs, $paths.Assignments, $paths.Results)) { [void][IO.Directory]::CreateDirectory($directory) }
    if (-not [IO.File]::Exists($paths.State)) { Write-RalphJsonAtomic $paths.State (New-RalphState $RepositoryRoot $Configuration (Get-RalphRepositoryIdentity $RepositoryRoot $Configuration)) (Join-Path $paths.Schemas 'state.schema.json') }
    if (-not [IO.File]::Exists($paths.Tasks)) { Write-RalphJsonAtomic $paths.Tasks ([ordered]@{schemaVersion='1.1';revision=0;planHash=$null;definitionHash=$null;status='not_planned';tasks=@()}) (Join-Path $paths.Schemas 'tasks.schema.json') }
    if (-not [IO.File]::Exists($paths.Bugs)) { Write-RalphJsonAtomic $paths.Bugs ([ordered]@{schemaVersion='1.2';revision=0;auditSha=$null;definitionHash=$null;status='not_audited';bugs=@()}) (Join-Path $paths.Schemas 'bugs.schema.json') }
    Update-RalphWorkflowStateSchema $RepositoryRoot $paths
    $paths
}

function Assert-RalphPlanDrift {
    param([Parameter(Mandatory)]$State, [Parameter(Mandatory)][string]$RepositoryRoot, [switch]$RequirePlan)
    $contractStatus = (Invoke-RalphNative git @('-C', $RepositoryRoot, 'status', '--porcelain', '--', 'requirements.md', 'plan.md')).Output
    if (-not [string]::IsNullOrWhiteSpace($contractStatus)) { throw 'requirements.md changed or plan.md changed in the coordinator checkout.' }
    if ($null -eq $State.targetBaseSha) {
        $requirementsHash = Get-RalphFileHash (Join-Path $RepositoryRoot 'requirements.md')
        $planHash = Get-RalphFileHash (Join-Path $RepositoryRoot 'plan.md')
    }
    else {
        $reference = if ($State.integrationSha) { [string]$State.integrationSha } else { [string]$State.targetBaseSha }
        $requirementsHash = Get-RalphGitBlobIdentity $RepositoryRoot $reference 'requirements.md'
        try { $planHash = Get-RalphGitBlobIdentity $RepositoryRoot $reference 'plan.md' } catch { $planHash = $null }
    }
    if ($RequirePlan -and $null -eq $planHash) { throw 'plan.md is required. Run the planning loop first.' }
    if ($null -ne $State.requirementsHash -and [string]$State.requirementsHash -cne $requirementsHash) { throw 'requirements.md at the recorded contract commit differs from state.json.' }
    if ($RequirePlan -and $null -ne $State.planHash -and [string]$State.planHash -cne $planHash) { throw 'plan.md at the recorded contract commit differs from state.json.' }
}

function ConvertTo-RalphStructuredBlocker {
    param($Blocker, [Parameter(Mandatory)][string]$Scope, [string]$Identity)
    if ($null -eq $Blocker) { return $null }
    $affected = if ($Identity -match '^(TASK|BUG)-[0-9]{4}$') { $Identity } else { $null }
    if ($Blocker -is [string]) {
        return [pscustomobject][ordered]@{
            kind = 'operational'; message = [string]$Blocker; evidence = [string]$Blocker
            affectedIdentity = $affected; requiresUserDecision = $false; scopeChangePossible = $false
            smallestResolution = 'Correct the reported operational failure and retry the bounded assignment.'
            prohibitedDecisions = @('Do not change project requirements or scope to bypass an operational failure.')
        }
    }
    $required = @('kind', 'message', 'evidence', 'affectedIdentity', 'requiresUserDecision', 'scopeChangePossible', 'smallestResolution', 'prohibitedDecisions')
    foreach ($name in $required) { if ($null -eq $Blocker.PSObject.Properties[$name]) { throw "Structured blocker is missing $name." } }
    if ([string]$Blocker.kind -notin @('operational', 'missing_information', 'contract_conflict', 'scope_gap', 'task_decomposition', 'bug_disposition')) { throw "Unsupported blocker kind: $($Blocker.kind)" }
    if ([string]::IsNullOrWhiteSpace([string]$Blocker.evidence)) { throw 'Structured blocker evidence is empty.' }
    if ([string]::IsNullOrWhiteSpace([string]$Blocker.smallestResolution)) { throw 'Structured blocker smallestResolution is empty.' }
    if ($affected -and [string]$Blocker.affectedIdentity -cne $affected) { throw "Structured blocker identity does not match $affected." }
    if (-not $affected -and $Blocker.affectedIdentity) { throw 'A workflow-level blocker cannot claim an unrelated task or bug identity.' }
    $Blocker
}

function Test-RalphSemanticBlocker {
    param($Blocker)
    $null -ne $Blocker -and [string]$Blocker.kind -cne 'operational' -and [bool]$Blocker.requiresUserDecision
}

function Get-RalphMaximumAmendmentRounds {
    param($Configuration)
    if ($null -eq $Configuration.PSObject.Properties['maximumAmendmentRounds']) { return 3 }
    $value = [int]$Configuration.maximumAmendmentRounds
    if ($value -lt 1 -or $value -gt 32) { throw 'maximumAmendmentRounds must be between 1 and 32.' }
    $value
}

function New-RalphPersistedTask {
    param([Parameter(Mandatory)]$Definition, [string]$AmendmentId)
    [pscustomobject][ordered]@{
        taskId=[string]$Definition.taskId;title=[string]$Definition.title;description=[string]$Definition.description;status='pending'
        requirementIds=@($Definition.requirementIds);planSections=@($Definition.planSections);dependencies=@($Definition.dependencies)
        allowedPaths=@($Definition.allowedPaths);exclusiveResources=@($Definition.exclusiveResources);acceptanceCriteria=@($Definition.acceptanceCriteria)
        checks=@($Definition.checks);attemptCount=0;branch=$null;worktree=$null;baseSha=$null;resultSha=$null;pullRequest=$null
        lastError=$null;amendmentId=if($AmendmentId){$AmendmentId}else{$null};supersededBy=@()
    }
}

function Assert-RalphPmAnalysis {
    param($Analysis, $Tasks, $Bugs, [string]$SourceStage)
    if (@($Analysis.options | Where-Object recommended).Count -ne 1) { throw 'PM analysis must identify exactly one recommended option.' }
    foreach ($taskId in @($Analysis.affectedTaskIds)) {
        if (@($Tasks.tasks | Where-Object taskId -eq $taskId).Count -ne 1) { throw "PM analysis references unknown task $taskId." }
    }
    foreach ($bugId in @($Analysis.affectedBugIds)) {
        if ($null -eq $Bugs -or @($Bugs.bugs | Where-Object bugId -eq $bugId).Count -ne 1) { throw "PM analysis references unknown bug $bugId." }
    }
    foreach ($option in @($Analysis.options)) {
        if ([bool]$option.requiresInput -and [string]::IsNullOrWhiteSpace([string]$option.inputPrompt)) { throw "PM option $($option.optionId) requires input but has no input prompt." }
        if ([string]$option.action -ceq 'disposition') {
            if ($SourceStage -cne 'audit' -or @($option.bugDispositions).Count -eq 0) { throw 'Bug disposition options are valid only during audit and must contain a disposition.' }
            if (@($option.authorizedDocumentationPaths).Count) { throw 'A disposition-only option cannot authorize documentation changes.' }
            foreach ($change in @($option.bugDispositions)) {
                if ([string]$change.bugId -notin @($Analysis.affectedBugIds)) { throw "Disposition for $($change.bugId) is not in the affected bug set." }
            }
        }
        elseif (@($option.bugDispositions).Count) { throw "PM option $($option.optionId) includes bug dispositions but is not a disposition action." }
    }
}

function Get-RalphAuthorizedDocumentationPaths {
    param($Option)
    $paths = @('requirements.md', 'plan.md') + @($Option.authorizedDocumentationPaths)
    $validated = foreach ($path in @($paths | Select-Object -Unique)) {
        $normalized = ([string]$path).Replace('\', '/').Trim()
        if (-not (Test-RalphSafeRelativePattern $normalized) -or $normalized -match '(^|/)[.]codex(/|$)' -or -not $normalized.EndsWith('.md', [StringComparison]::OrdinalIgnoreCase)) { throw "PM documentation path is not a safe Markdown path: $path" }
        $normalized
    }
    @($validated)
}

function New-RalphDecisionIdentity {
    param([string]$AmendmentId, [string]$OptionId, [string]$Response, [string]$Question)
    Get-RalphObjectHash ([ordered]@{amendmentId=$AmendmentId;optionId=$OptionId;response=$Response;question=$Question})
}

function New-RalphAmendmentWorktree {
    param([string]$RepositoryRoot, $Configuration, [string]$Identity, [string]$BaseReference, [string]$ExpectedHeadSha)
    if ($Identity -notmatch '^AMEND-[0-9]{4}$') { throw "Invalid amendment identity: $Identity" }
    $branch = "worktree/$Identity"
    $base = Get-RalphWorktreeBase $RepositoryRoot $Configuration
    [void][IO.Directory]::CreateDirectory($base)
    $path = [IO.Path]::GetFullPath((Join-Path $base $Identity))
    if ([IO.Directory]::Exists($path)) {
        $actual = (Invoke-RalphNative git @('-C', $path, 'branch', '--show-current')).Output.Trim()
        if ($actual -cne $branch) { throw "Existing amendment worktree has branch $actual." }
        $status = (Invoke-RalphNative git @('-C', $path, 'status', '--porcelain', '--untracked-files=all')).Output
        if (-not [string]::IsNullOrWhiteSpace($status)) { throw 'Amendment worktree contains uncommitted changes.' }
        $head = (Invoke-RalphNative git @('-C', $path, 'rev-parse', 'HEAD')).Output.Trim()
        if ($ExpectedHeadSha -and $head -cne $ExpectedHeadSha) { throw 'Amendment worktree HEAD differs from its recorded result.' }
        if ((Invoke-RalphNative git @('-C', $path, 'merge-base', '--is-ancestor', $BaseReference, $head) -AllowedExitCodes @(0,1)).ExitCode -ne 0) { throw 'Amendment branch does not descend from its recorded base.' }
        return $path
    }
    $exists = (Invoke-RalphNative git @('-C', $RepositoryRoot, 'show-ref', '--verify', '--quiet', "refs/heads/$branch") -AllowedExitCodes @(0,1)).ExitCode -eq 0
    if ($exists) { [void](Invoke-RalphNative git @('-C', $RepositoryRoot, 'worktree', 'add', '--', $path, $branch)) }
    else { [void](Invoke-RalphNative git @('-C', $RepositoryRoot, 'worktree', 'add', '-b', $branch, '--', $path, $BaseReference)) }
    $path
}

function Assert-RalphAmendmentCommit {
    param([string]$Worktree, [string]$BaseSha, [string[]]$AuthorizedPaths)
    $status = (Invoke-RalphNative git @('-C', $Worktree, 'status', '--porcelain', '--untracked-files=all')).Output
    if (-not [string]::IsNullOrWhiteSpace($status)) { throw 'PM amendment worktree is not clean.' }
    $head = (Invoke-RalphNative git @('-C', $Worktree, 'rev-parse', 'HEAD')).Output.Trim()
    if ($head -ceq $BaseSha -or (Invoke-RalphNative git @('-C', $Worktree, 'merge-base', '--is-ancestor', $BaseSha, $head) -AllowedExitCodes @(0,1)).ExitCode -ne 0) { throw 'PM did not create a descendant amendment commit.' }
    $commitCount = [int](Invoke-RalphNative git @('-C', $Worktree, 'rev-list', '--count', "$BaseSha..$head")).Output.Trim()
    if ($commitCount -ne 1) { throw "PM amendment must contain exactly one commit; found $commitCount." }
    $changed = @((Invoke-RalphNative git @('-C', $Worktree, 'diff', '--name-only', "$BaseSha..$head")).Lines | Where-Object { $_ })
    foreach ($path in $changed) { if ([string]$path -notin $AuthorizedPaths) { throw "PM modified an unauthorized path: $path" } }
    if (-not $changed) { throw 'PM amendment commit changed no approved documentation.' }
    [pscustomobject]@{Head=$head;ChangedFiles=$changed}
}

function Assert-RalphPmResultIdentity {
    param([Parameter(Mandatory)]$Result,[Parameter(Mandatory)]$Amendment,[Parameter(Mandatory)]$Commit)
    if ([string]$Result.decisionIdentity -cne [string]$Amendment.decisionIdentity -or [string]$Result.selectedOptionId -cne [string]$Amendment.selectedOptionId) { throw 'PM result does not match the approved user decision.' }
    if ([string]$Result.commitSha -cne [string]$Commit.Head) { throw 'PM result commit SHA does not match amendment worktree HEAD.' }
    if (Compare-Object @($Commit.ChangedFiles|Sort-Object) @($Result.changedFiles|Sort-Object)) { throw 'PM result changedFiles does not match the amendment commit.' }
}
function Save-RalphPmTaskLedger { param($Tasks,$Paths) $Tasks.revision=[int]$Tasks.revision+1;Write-RalphJsonAtomic $Paths.Tasks $Tasks (Join-Path $Paths.Schemas 'tasks.schema.json') }
function Save-RalphPmBugLedger { param($Bugs,$Paths) $Bugs.revision=[int]$Bugs.revision+1;Write-RalphJsonAtomic $Paths.Bugs $Bugs (Join-Path $Paths.Schemas 'bugs.schema.json') }
function Get-RalphAmendmentAnalysisPath { param($Paths,[string]$Identity) Join-Path $Paths.Results "$Identity-analysis.json" }

function Complete-RalphDispositionDecision {
    param($RepositoryRoot,$Configuration,$State,$Paths,$Tasks,$Bugs,$Amendment,$Option)
    $ledgerChanged = $false
    foreach ($change in @($Option.bugDispositions)) {
        $matches = @($Bugs.bugs | Where-Object bugId -eq $change.bugId)
        if ($matches.Count -ne 1) { throw "Disposition references unknown bug $($change.bugId)." }
        $bug = $matches[0]
        if ([string]$bug.status -eq 'verified') {
            if ([string]$bug.disposition -cne [string]$change.disposition -or [string]$bug.amendmentId -cne [string]$Amendment.amendmentId -or [string]$bug.dispositionEvidence -cne [string]$change.evidence) {
                throw "Bug $($bug.bugId) was verified by a different decision."
            }
            continue
        }
        $bug.disposition = [string]$change.disposition
        $bug.status = 'verified'
        $bug.amendmentId = [string]$Amendment.amendmentId
        $bug.lastError = $null
        $bug.dispositionEvidence = [string]$change.evidence
        $ledgerChanged = $true
    }
    if ($ledgerChanged) {
        $Bugs.definitionHash = Get-RalphDefinitionHash @($Bugs.bugs) bug
        $State.bugDefinitionHash = [string]$Bugs.definitionHash
        Save-RalphPmBugLedger $Bugs $Paths
    }
    if ([string]$Amendment.status -cne 'applied') {
        $Amendment.status = 'applied'
        $State.stage = 'audit'; $State.stageStatus = 'running'; Save-RalphState $State $Paths
    }
    Remove-RalphWorktree $RepositoryRoot $Configuration ([string]$Amendment.amendmentId) ([string]$Amendment.branch)
    $State.activeAmendment = $null; $State.blocker = $null; Save-RalphState $State $Paths
    [pscustomobject]@{Action='disposition';ResumeStage='audit';AmendmentId=[string]$Amendment.amendmentId;SourceSuperseded=$true}
}

function Invoke-RalphPmResolution {
    param(
        [Parameter(Mandatory)][string]$RepositoryRoot, [Parameter(Mandatory)]$Configuration,
        [Parameter(Mandatory)]$State, [Parameter(Mandatory)]$Paths, [Parameter(Mandatory)]$Tasks, $Bugs,
        [ValidateSet('build','audit')][string]$SourceStage,
        [ValidateSet('task','bug','verification','audit')][string]$SourceKind,
        [string]$SourceIdentity, $Blocker, [scriptblock]$InputReader
    )
    if ($null -eq $State.activeAmendment) {
        $structured = ConvertTo-RalphStructuredBlocker $Blocker $SourceStage $SourceIdentity
        if (-not (Test-RalphSemanticBlocker $structured)) { throw "Operational blocker requires correction, not a PM decision: $($structured.message)" }
        $next = [int]$State.amendmentSequence + 1
        if ($next -gt (Get-RalphMaximumAmendmentRounds $Configuration)) { throw "Semantic amendment limit exhausted at $next attempts." }
        $State.amendmentSequence = $next
        $identity = 'AMEND-{0:D4}' -f $next
        $State.activeAmendment = [pscustomobject][ordered]@{
            amendmentId=$identity;sourceStage=$SourceStage;sourceKind=$SourceKind;sourceIdentity=if($SourceIdentity){$SourceIdentity}else{$null}
            status='analyzing';blocker=$structured;analysisResultPath=$null;selectedOptionId=$null;userResponse=$null
            decisionIdentity=$null;authorizedDocumentationPaths=@();branch="worktree/$identity";worktree=$null;baseSha=$null
            resultSha=$null;pullRequest=$null;affectedTaskIds=@();affectedBugIds=@();resumeStage=$SourceStage;attemptCount=0
        }
        $State.stage=$SourceStage;$State.stageStatus='amending';Save-RalphState $State $Paths
    }

    $amendment = $State.activeAmendment
    $identity = [string]$amendment.amendmentId
    $analysisPath = Get-RalphAmendmentAnalysisPath $Paths $identity
    if ([string]$amendment.status -ceq 'analyzing') {
        $known = @(@($Tasks.tasks) + @(if($null-ne$Bugs){$Bugs.bugs}else{@()}) | Where-Object {$_.pullRequest -and $_.pullRequest.mergeSha} | ForEach-Object {[string]$_.pullRequest.mergeSha})
        $State.integrationSha = Ensure-RalphIntegrationBranch $RepositoryRoot $Configuration $State $known
        $amendment.baseSha = [string]$State.integrationSha
        $amendment.worktree = New-RalphAmendmentWorktree $RepositoryRoot $Configuration $identity ([string]$amendment.baseSha) $null
        if ([IO.File]::Exists($analysisPath)) {
            $analysis = Read-RalphJson $analysisPath (Join-Path $Paths.Schemas 'pm-blocker-result.schema.json')
        }
        else {
            $context = "Mode: analyze`nAmendment: $identity`nSource stage: $SourceStage`nSource identity: $SourceIdentity`nExact integration SHA: $($State.integrationSha)`nStructured blocker:`n$($amendment.blocker|ConvertTo-Json -Depth 20)`nTask ledger:`n$($Tasks|ConvertTo-Json -Depth 30)`nBug ledger:`n$(if($null-ne$Bugs){$Bugs|ConvertTo-Json -Depth 30}else{'not available'})"
            $analysis = Invoke-RalphRole $RepositoryRoot ([string]$amendment.worktree) 'project-manager' $context 'pm-blocker-result.schema.json' 'read-only'
            Write-RalphImmutableJson $analysisPath $analysis
        }
        Assert-RalphPmAnalysis $analysis $Tasks $Bugs $SourceStage
        $amendment.analysisResultPath=$analysisPath;$amendment.affectedTaskIds=@($analysis.affectedTaskIds);$amendment.affectedBugIds=@($analysis.affectedBugIds)
        $amendment.status='awaiting_user';$State.stageStatus='awaiting_user';Save-RalphState $State $Paths
    }

    $analysis = Read-RalphJson ([string]$amendment.analysisResultPath) (Join-Path $Paths.Schemas 'pm-blocker-result.schema.json')
    Assert-RalphPmAnalysis $analysis $Tasks $Bugs $SourceStage
    if ([string]$amendment.status -ceq 'applied') {
        $appliedOption = @($analysis.options | Where-Object optionId -eq ([string]$amendment.selectedOptionId))
        if ($appliedOption.Count -eq 1 -and [string]$appliedOption[0].action -ceq 'disposition') {
            return Complete-RalphDispositionDecision $RepositoryRoot $Configuration $State $Paths $Tasks $Bugs $amendment $appliedOption[0]
        }
    }
    if ([string]$amendment.status -ceq 'awaiting_user') {
        if ($amendment.selectedOptionId) { $selection = [string]$amendment.selectedOptionId }
        else {
            Write-Host '';Write-Host "PM DECISION REQUIRED: $identity";Write-Host $analysis.summary;Write-Host "Recommendation: $($analysis.recommendation)"
            Write-Host "Effects: requirements=$($analysis.effects.requirements); plan=$($analysis.effects.plan); tasks=$($analysis.effects.tasks); bugs=$($analysis.effects.bugs); completed work=$($analysis.effects.completedWork); schedule=$($analysis.effects.schedule)"
            foreach ($option in @($analysis.options)) { Write-Host "  $($option.optionId): $($option.label)$(if($option.recommended){' [recommended]'})";Write-Host "    $($option.description)" }
            Write-Host $analysis.question
            $selection = if ($null -eq $InputReader) { Read-Host 'Select an option ID' } else { & $InputReader $analysis 'option' $null }
            if ([string]::IsNullOrWhiteSpace([string]$selection)) { throw 'No PM option was selected.' }
            $selection = ([string]$selection).Trim().ToUpperInvariant()
            $selected = @($analysis.options | Where-Object optionId -eq $selection)
            if ($selected.Count -ne 1) { throw "Unknown PM option: $selection" }
            $response = $selection
            if ([bool]$selected[0].requiresInput) {
                $response = if ($null -eq $InputReader) { Read-Host ([string]$selected[0].inputPrompt) } else { & $InputReader $analysis 'value' $selected[0] }
                if ([string]::IsNullOrWhiteSpace([string]$response)) { throw "PM option $selection requires a nonempty response." }
                if ([string]$response.Length -gt 4096) { throw 'PM response exceeds 4096 characters.' }
                $response = ([string]$response).Trim()
            }
            $amendment.selectedOptionId=$selection;$amendment.userResponse=$response
            $amendment.decisionIdentity=New-RalphDecisionIdentity $identity $selection $response ([string]$analysis.question)
            Save-RalphState $State $Paths
        }
        $option = @($analysis.options | Where-Object optionId -eq $selection)
        if ($option.Count -ne 1) { throw "Unknown PM option: $selection" }
        if ([string]$option[0].action -ceq 'stop') { throw "User selected stop for $identity." }
        if ([string]$option[0].action -ceq 'retry') {
            Remove-RalphWorktree $RepositoryRoot $Configuration $identity ([string]$amendment.branch)
            $State.activeAmendment=$null;$State.blocker=$null;$State.stage=$SourceStage;$State.stageStatus='running';Save-RalphState $State $Paths
            return [pscustomobject]@{Action='retry';ResumeStage=$SourceStage;AmendmentId=$identity;SourceSuperseded=$false}
        }
        if ([string]$option[0].action -ceq 'disposition') { return Complete-RalphDispositionDecision $RepositoryRoot $Configuration $State $Paths $Tasks $Bugs $amendment $option[0] }
        if (-not [bool]$analysis.amendmentRequired) { throw 'The selected amendment conflicts with PM analysis stating that no amendment is required.' }
        $amendment.authorizedDocumentationPaths = Get-RalphAuthorizedDocumentationPaths $option[0]
        $amendment.status='approved';$State.stageStatus='amending';Save-RalphState $State $Paths
    }

    $resultPath = Get-RalphAttemptPath $Paths result $identity 1
    if ([string]$amendment.status -in @('approved','agent_active')) {
        $amendment.worktree = New-RalphAmendmentWorktree $RepositoryRoot $Configuration $identity ([string]$amendment.baseSha) ([string]$amendment.resultSha)
        $amendment.attemptCount=1;$amendment.status='agent_active';Save-RalphState $State $Paths
        $assignmentPath = Get-RalphAttemptPath $Paths assignment $identity 1
        if (-not [IO.File]::Exists($assignmentPath)) {
            $assignment=[ordered]@{schemaVersion='1.0';identity=$identity;attempt=1;baseSha=[string]$amendment.baseSha;selectedOptionId=[string]$amendment.selectedOptionId;userResponse=[string]$amendment.userResponse;decisionIdentity=[string]$amendment.decisionIdentity;authorizedDocumentationPaths=@($amendment.authorizedDocumentationPaths);blocker=$amendment.blocker;createdAt=[DateTimeOffset]::UtcNow.ToString('O')}
            Write-RalphImmutableJson $assignmentPath $assignment
        }
        if ([IO.File]::Exists($resultPath)) {
            $record = Read-RalphJson $resultPath
        }
        else {
            $head = (Invoke-RalphNative git @('-C', [string]$amendment.worktree, 'rev-parse', 'HEAD')).Output.Trim()
            $mode = if ($head -cne [string]$amendment.baseSha) { 'recover_result' } else { 'amend' }
            $sandbox = if ($mode -ceq 'recover_result') { 'read-only' } else { 'workspace-write' }
            $context = "Mode: $mode`nAmendment: $identity`nApproved option: $($amendment.selectedOptionId)`nUser response: $($amendment.userResponse)`nDecision identity: $($amendment.decisionIdentity)`nAuthorized documentation paths: $(@($amendment.authorizedDocumentationPaths)-join', ')`nBase SHA: $($amendment.baseSha)`nAnalysis:`n$($analysis|ConvertTo-Json -Depth 30)`nCurrent tasks:`n$($Tasks|ConvertTo-Json -Depth 30)"
            $result = Invoke-RalphRole $RepositoryRoot ([string]$amendment.worktree) 'project-manager' $context 'pm-amendment-result.schema.json' $sandbox
            if ([string]$result.status -cne 'completed') { throw "PM amendment blocked: $($result.blocker)" }
            $record=[ordered]@{schemaVersion='1.0';identity=$identity;attempt=1;succeeded=$true;result=$result;error=$null;completedAt=[DateTimeOffset]::UtcNow.ToString('O')}
            Write-RalphImmutableJson $resultPath $record
        }
        $amendment.status='result_ready';Save-RalphState $State $Paths
    }

    $record = Read-RalphJson $resultPath
    $result = $record.result
    if ([string]$amendment.status -ceq 'result_ready') {
        $commit=Assert-RalphAmendmentCommit ([string]$amendment.worktree) ([string]$amendment.baseSha) @($amendment.authorizedDocumentationPaths)
        Assert-RalphPmResultIdentity $result $amendment $commit
        $amendment.resultSha=[string]$commit.Head
        $numbers=@($Tasks.tasks|ForEach-Object{[int](([string]$_.taskId).Substring(5))});$expectedStart=if($numbers.Count){($numbers|Measure-Object -Maximum).Maximum+1}else{1}
        for($i=0;$i-lt@($result.newTasks).Count;$i++){if([string]$result.newTasks[$i].taskId-cne('TASK-{0:D4}' -f ([int]($expectedStart + $i)))){throw 'PM follow-up task IDs must append monotonically.'}}
        if (@($result.supersededTaskIds).Count -and @($result.newTasks).Count -eq 0) { throw 'A superseded task requires at least one replacement follow-up task.' }
        foreach($taskId in @($result.supersededTaskIds)){
            $existing=@($Tasks.tasks|Where-Object taskId -eq $taskId);if($existing.Count-ne1){throw "PM superseded an unknown task: $taskId"}
            if([string]$existing[0].status-in@('integrated','active','submitted')){throw "PM cannot supersede task $taskId because it is active, integrated, or submitted."}
            if($existing[0].resultSha){
                $durable=Read-RalphAttemptResult $Paths ([string]$existing[0].taskId) ([int]$existing[0].attemptCount)
                if($null-eq$durable-or-not[bool]$durable.succeeded-or[string]$existing[0].status-notin@('result_ready','verified_ready')){throw "PM cannot supersede task $taskId because its unintegrated result is not durably preserved."}
            }
            if($existing[0].worktree-and[IO.Directory]::Exists([string]$existing[0].worktree)){$status=(Invoke-RalphNative git @('-C',[string]$existing[0].worktree,'status','--porcelain','--untracked-files=all')).Output;$head=(Invoke-RalphNative git @('-C',[string]$existing[0].worktree,'rev-parse','HEAD')).Output.Trim();if(-not[string]::IsNullOrWhiteSpace($status)-or($head-cne[string]$existing[0].baseSha-and(-not$existing[0].resultSha-or$head-cne[string]$existing[0].resultSha))){throw "PM cannot supersede task $taskId because its worktree contains unrecorded work."}}
        }
        $candidate=@($Tasks.tasks|ForEach-Object{$_})+@($result.newTasks|ForEach-Object{New-RalphPersistedTask $_ $identity});Assert-RalphGraph $candidate task
        $superseded=@($result.supersededTaskIds);foreach($task in $candidate|Where-Object{$_.taskId-notin$superseded}){if(@($task.dependencies|Where-Object{$_-in$superseded}).Count){throw "$($task.taskId) depends on a superseded task."}}
        $requirements=Read-RalphGitText ([string]$amendment.worktree) ([string]$commit.Head) 'requirements.md';Assert-RalphTaskCoverage @($candidate|Where-Object{$_.taskId-notin$superseded}) $requirements
        $verificationContext="Verify this approved project amendment against the exact user decision. Reject unrelated requirements, excluded or deferred scope, unsupported task definitions, or documentation that does not implement the decision.`nDecision identity: $($amendment.decisionIdentity)`nSelected option: $($amendment.selectedOptionId)`nUser response: $($amendment.userResponse)`nPM result:`n$($result|ConvertTo-Json -Depth 50)"
        $verification=Invoke-RalphRole $RepositoryRoot ([string]$amendment.worktree) 'verifier' $verificationContext 'verifier-result.schema.json' 'read-only'
        if(-not[bool]$verification.approved){throw "PM amendment semantic verification failed: $(@($verification.findings)-join'; ')"}
        [void](Invoke-RalphNative git @('-C',[string]$amendment.worktree,'push','--set-upstream',[string]$Configuration.remote,[string]$amendment.branch))
        [void](Invoke-RalphNative git @('-C',$RepositoryRoot,'fetch',[string]$Configuration.remote,'--prune'))
        $base=(Invoke-RalphNative git @('-C',$RepositoryRoot,'rev-parse',"$($Configuration.remote)/$($Configuration.integrationBranch)")).Output.Trim()
        $pr=New-RalphPullRequest $RepositoryRoot $Configuration ([string]$amendment.branch) ([string]$Configuration.integrationBranch) ([string]$amendment.resultSha) $base "$identity project contract amendment" ([string]$result.summary)
        $amendment.pullRequest=$pr;$amendment.status='submitted';Save-RalphState $State $Paths
    }
    if([string]$amendment.status-ceq'submitted'){
        $pr=Get-RalphPullRequest $RepositoryRoot $Configuration ([string]$amendment.branch) ([string]$Configuration.integrationBranch) ([string]$amendment.resultSha) ([string]$amendment.pullRequest.id)
        if($null-eq$pr){throw 'Unable to reconcile the amendment pull request.'}
        $merged=Complete-RalphPullRequest $RepositoryRoot $Configuration $pr;$amendment.pullRequest=$merged;$amendment.status='integrated'
        $State.integrationSha=[string]$merged.mergeSha;$State.acceptedIntegrationShas=@(@($State.acceptedIntegrationShas)+@([string]$merged.mergeSha)|Select-Object -Unique);Save-RalphState $State $Paths
    }
    if([string]$amendment.status-ceq'integrated'){
        $newIds=@($result.newTasks|ForEach-Object{[string]$_.taskId});foreach($taskId in @($result.supersededTaskIds)){$task=@($Tasks.tasks|Where-Object taskId -eq $taskId)[0];$task.status='superseded';$task.amendmentId=$identity;$task.supersededBy=@($newIds)}
        foreach($definition in @($result.newTasks)){if(@($Tasks.tasks|Where-Object taskId -eq $definition.taskId).Count-eq0){$Tasks.tasks=@($Tasks.tasks)+@(New-RalphPersistedTask $definition $identity)}}
        $State.requirementsHash=Get-RalphGitBlobIdentity $RepositoryRoot ([string]$State.integrationSha) 'requirements.md';$State.planHash=Get-RalphGitBlobIdentity $RepositoryRoot ([string]$State.integrationSha) 'plan.md'
        $Tasks.planHash=[string]$State.planHash;$Tasks.definitionHash=Get-RalphDefinitionHash @($Tasks.tasks) task;$Tasks.status='active';$State.taskDefinitionHash=[string]$Tasks.definitionHash;Save-RalphPmTaskLedger $Tasks $Paths
        $resume=if($SourceStage-ceq'build'){'build'}else{[string]$result.resumeStage};if(@($result.newTasks).Count-or@($result.supersededTaskIds).Count){$resume='build'}
        if($SourceStage-ceq'audit'-and$null-ne$Bugs){$accepted=@($State.acceptedIntegrationShas)+@($Bugs.bugs|Where-Object{$_.pullRequest-and$_.pullRequest.mergeSha}|ForEach-Object{[string]$_.pullRequest.mergeSha});$State.acceptedIntegrationShas=@($accepted|Select-Object -Unique);$history=Join-Path $Paths.Results "$identity-pre-expansion-audit.json";if(-not[IO.File]::Exists($history)){Write-RalphImmutableJson $history $Bugs};$Bugs.schemaVersion='1.2';$Bugs.revision=[int]$Bugs.revision+1;$Bugs.auditSha=$null;$Bugs.definitionHash=$null;$Bugs.status='not_audited';$Bugs.bugs=@();Write-RalphJsonAtomic $Paths.Bugs $Bugs (Join-Path $Paths.Schemas 'bugs.schema.json');$State.bugDefinitionHash=$null}
        $amendment.resumeStage=$resume;$amendment.status='applied';$State.stage=$resume;$State.stageStatus='amending';Save-RalphState $State $Paths
    }
    if([string]$amendment.status-ceq'applied'){
        Remove-RalphMergedAssignment $RepositoryRoot $Configuration $identity ([string]$amendment.branch) $amendment.pullRequest
        $sourceSuperseded=$SourceIdentity-in@($result.supersededTaskIds);$resume=[string]$amendment.resumeStage
        $State.activeAmendment=$null;$State.blocker=$null;$State.stage=$resume;$State.stageStatus='running';Save-RalphState $State $Paths
        return [pscustomobject]@{Action='amended';ResumeStage=$resume;AmendmentId=$identity;SourceSuperseded=$sourceSuperseded}
    }
    throw "Unsupported amendment state: $($amendment.status)"
}
