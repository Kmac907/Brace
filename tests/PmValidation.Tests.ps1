. (Join-Path $PSScriptRoot 'TestSupport.ps1')
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'template/.codex/scripts/common.ps1')

$task = [pscustomobject]@{ taskId = 'TASK-0001' }
$tasks = [pscustomobject]@{ tasks = @($task) }
$bug = [pscustomobject]@{ bugId = 'BUG-0001' }
$bugs = [pscustomobject]@{ bugs = @($bug) }

$operational = ConvertTo-RalphStructuredBlocker 'provider unavailable' build 'TASK-0001'
Assert-TestEqual 'operational' ([string]$operational.kind) 'legacy operational errors remain operational'
Assert-TestTrue (-not (Test-RalphSemanticBlocker $operational)) 'operational blockers do not invoke the PM'

$semantic = [pscustomobject]@{
    kind = 'scope_gap'
    message = 'Missing requirement.'
    evidence = 'The task has no governing requirement.'
    affectedIdentity = 'TASK-0001'
    requiresUserDecision = $true
    scopeChangePossible = $true
    smallestResolution = 'Ask whether to add the requirement.'
    prohibitedDecisions = @('Do not invent product behavior.')
}
Assert-TestTrue (Test-RalphSemanticBlocker (ConvertTo-RalphStructuredBlocker $semantic build 'TASK-0001')) 'semantic blocker invokes the PM'
Assert-TestThrows { ConvertTo-RalphStructuredBlocker ([pscustomobject]@{ kind='scope_gap';message='x' }) build 'TASK-0001' } 'missing evidence'
$wrongIdentity = $semantic.PSObject.Copy(); $wrongIdentity.affectedIdentity = 'TASK-0002'
Assert-TestThrows { ConvertTo-RalphStructuredBlocker $wrongIdentity build 'TASK-0001' } 'identity does not match'

function New-TestPmAnalysis {
    $effects = [pscustomobject]@{ requirements='No change.';plan='No change.';tasks='No change.';bugs='No change.';completedWork='No change.';schedule='No change.' }
    $option = [pscustomobject]@{ optionId='OPTION-0001';label='Retry';description='Retry unchanged.';recommended=$true;action='retry';requiresInput=$false;inputPrompt=$null;authorizedDocumentationPaths=@();bugDispositions=@() }
    [pscustomobject]@{ summary='Analyze blocker.';recommendation='Retry.';question='Retry?';amendmentRequired=$false;effects=$effects;options=@($option);affectedTaskIds=@('TASK-0001');affectedBugIds=@() }
}

$analysis = New-TestPmAnalysis
Assert-RalphPmAnalysis $analysis $tasks $bugs build
Assert-TestTrue $true 'valid PM analysis accepted'
$analysis = New-TestPmAnalysis; $analysis.options += $analysis.options[0].PSObject.Copy()
Assert-TestThrows { Assert-RalphPmAnalysis $analysis $tasks $bugs build } 'exactly one recommended'
$analysis = New-TestPmAnalysis; $analysis.affectedTaskIds = @('TASK-9999')
Assert-TestThrows { Assert-RalphPmAnalysis $analysis $tasks $bugs build } 'unknown task'
$analysis = New-TestPmAnalysis; $analysis.options[0].requiresInput = $true
Assert-TestThrows { Assert-RalphPmAnalysis $analysis $tasks $bugs build } 'requires input but has no input prompt'
$analysis = New-TestPmAnalysis; $analysis.options[0].action='disposition';$analysis.options[0].bugDispositions=@([pscustomobject]@{bugId='BUG-0001';disposition='superseded';evidence='Decision evidence.'});$analysis.affectedBugIds=@('BUG-0001')
Assert-TestThrows { Assert-RalphPmAnalysis $analysis $tasks $bugs build } 'only during audit'
Assert-RalphPmAnalysis $analysis $tasks $bugs audit
Assert-TestTrue $true 'audit-only bug disposition accepted'

$option = [pscustomobject]@{ authorizedDocumentationPaths=@('docs/approved.md') }
$authorized = @(Get-RalphAuthorizedDocumentationPaths $option)
Assert-TestTrue ('requirements.md' -in $authorized -and 'plan.md' -in $authorized -and 'docs/approved.md' -in $authorized) 'approved Markdown paths are normalized'
$option.authorizedDocumentationPaths=@('src/main.rs')
Assert-TestThrows { Get-RalphAuthorizedDocumentationPaths $option } 'safe Markdown path'
$option.authorizedDocumentationPaths=@('.codex/AGENTS.md')
Assert-TestThrows { Get-RalphAuthorizedDocumentationPaths $option } 'safe Markdown path'

$configuration = [pscustomobject]@{ maximumAmendmentRounds = 3 }
Assert-TestEqual 3 (Get-RalphMaximumAmendmentRounds $configuration) 'amendment limit accepted'
$configuration.maximumAmendmentRounds = 0
Assert-TestThrows { Get-RalphMaximumAmendmentRounds $configuration } 'between 1 and 32'

$identityA = New-RalphDecisionIdentity 'AMEND-0001' 'OPTION-0001' 'yes' 'Proceed?'
$identityB = New-RalphDecisionIdentity 'AMEND-0001' 'OPTION-0001' 'yes' 'Proceed?'
$identityC = New-RalphDecisionIdentity 'AMEND-0001' 'OPTION-0002' 'no' 'Proceed?'
Assert-TestEqual $identityA $identityB 'decision identity is deterministic'
Assert-TestTrue ($identityA -cne $identityC) 'decision identity binds the selected option'

$graph = @(
    [pscustomobject]@{taskId='TASK-0001';dependencies=@();allowedPaths=@('src/one/**');exclusiveResources=@()},
    [pscustomobject]@{taskId='TASK-0002';dependencies=@('TASK-0001');allowedPaths=@('src/two/**');exclusiveResources=@()}
)
Assert-RalphGraph $graph task
Assert-TestTrue $true 'valid follow-up dependency graph accepted'
$graph[1].dependencies=@('TASK-9999')
Assert-TestThrows { Assert-RalphGraph $graph task } 'unknown task TASK-9999'
$graph[1].dependencies=@('TASK-0001');$graph[0].dependencies=@('TASK-0002')
Assert-TestThrows { Assert-RalphGraph $graph task } 'cycle'

$amendmentIdentity = [pscustomobject]@{decisionIdentity=('sha256:' + ('a' * 64));selectedOptionId='OPTION-0001'}
$commitIdentity = [pscustomobject]@{Head=('b' * 40);ChangedFiles=@('plan.md','requirements.md')}
$resultIdentity = [pscustomobject]@{decisionIdentity=$amendmentIdentity.decisionIdentity;selectedOptionId='OPTION-0001';commitSha=$commitIdentity.Head;changedFiles=@('requirements.md','plan.md')}
Assert-RalphPmResultIdentity $resultIdentity $amendmentIdentity $commitIdentity
Assert-TestTrue $true 'exact PM decision and commit identity accepted'
$resultIdentity.commitSha = 'c' * 40
Assert-TestThrows { Assert-RalphPmResultIdentity $resultIdentity $amendmentIdentity $commitIdentity } 'commit SHA does not match'
$resultIdentity.commitSha = $commitIdentity.Head; $resultIdentity.decisionIdentity = 'sha256:' + ('d' * 64)
Assert-TestThrows { Assert-RalphPmResultIdentity $resultIdentity $amendmentIdentity $commitIdentity } 'approved user decision'
$resultIdentity.decisionIdentity = $amendmentIdentity.decisionIdentity; $resultIdentity.changedFiles = @('plan.md')
Assert-TestThrows { Assert-RalphPmResultIdentity $resultIdentity $amendmentIdentity $commitIdentity } 'changedFiles does not match'

& {
    function Save-RalphState { param($State,$Paths) }
    function Remove-RalphWorktree { param($RepositoryRoot,$Configuration,$Identity,$Branch) }
    $appliedAmendment = [pscustomobject]@{ amendmentId='AMEND-0001';branch='worktree/AMEND-0001';status='applied' }
    $appliedState = [pscustomobject]@{ activeAmendment=$appliedAmendment;blocker='preserved';stage='audit';stageStatus='running';bugDefinitionHash='sha256:test' }
    $appliedBug = [pscustomobject]@{ bugId='BUG-0001';status='verified';disposition='superseded';amendmentId='AMEND-0001';dispositionEvidence='Approved evidence.' }
    $appliedBugs = [pscustomobject]@{ bugs=@($appliedBug) }
    $dispositionOption = [pscustomobject]@{ bugDispositions=@([pscustomobject]@{bugId='BUG-0001';disposition='superseded';evidence='Approved evidence.'}) }
    $recovered = Complete-RalphDispositionDecision 'repository' ([pscustomobject]@{}) $appliedState ([pscustomobject]@{}) $tasks $appliedBugs $appliedAmendment $dispositionOption
    Assert-TestEqual 'disposition' ([string]$recovered.Action) 'applied disposition replay is idempotent'
    Assert-TestTrue ($null -eq $appliedState.activeAmendment) 'applied disposition replay clears amendment state after cleanup'

    $conflictingAmendment = [pscustomobject]@{ amendmentId='AMEND-0002';branch='worktree/AMEND-0002';status='applied' }
    $conflictingState = [pscustomobject]@{ activeAmendment=$conflictingAmendment;blocker='preserved';stage='audit';stageStatus='running';bugDefinitionHash='sha256:test' }
    Assert-TestThrows {
        Complete-RalphDispositionDecision 'repository' ([pscustomobject]@{}) $conflictingState ([pscustomobject]@{}) $tasks $appliedBugs $conflictingAmendment $dispositionOption
    } 'verified by a different decision'
}

$schemaRoot = Join-Path (Split-Path $PSScriptRoot -Parent) 'template/.codex/schemas'
$validVerifierBlocker = '{"approved":false,"summary":"blocked","findings":[],"checks":[],"blocker":{"kind":"scope_gap","message":"x","evidence":"e","affectedIdentity":"TASK-0001","requiresUserDecision":true,"scopeChangePossible":true,"smallestResolution":"ask","prohibitedDecisions":[]}}'
Test-RalphJsonDocument $validVerifierBlocker (Join-Path $schemaRoot 'verifier-result.schema.json')
Assert-TestTrue $true 'verifier accepts a structured blocker when approval is false'
$invalidApprovedBlocker = $validVerifierBlocker.Replace('"approved":false','"approved":true')
Assert-TestThrows { Test-RalphJsonDocument $invalidApprovedBlocker (Join-Path $schemaRoot 'verifier-result.schema.json') } 'should be.*null'
$invalidBuilderSuccess = '{"status":"completed","summary":"done","commitSha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","changedFiles":[],"checks":[],"blocker":{"kind":"scope_gap","message":"x","evidence":"e","affectedIdentity":"TASK-0001","requiresUserDecision":true,"scopeChangePossible":true,"smallestResolution":"ask","prohibitedDecisions":[]}}'
Assert-TestThrows { Test-RalphJsonDocument $invalidBuilderSuccess (Join-Path $schemaRoot 'builder-result.schema.json') } 'should be.*null'

$temporary = New-TestDirectory
try {
    & git init -b main $temporary | Out-Null
    & git -C $temporary config user.name 'Worktree Ralph Test'
    & git -C $temporary config user.email 'test@example.invalid'
    [IO.File]::WriteAllText((Join-Path $temporary 'requirements.md'), 'one')
    [IO.File]::WriteAllText((Join-Path $temporary 'plan.md'), 'one')
    & git -C $temporary add --all
    & git -C $temporary commit -m base | Out-Null
    $base = (& git -C $temporary rev-parse HEAD).Trim()
    [IO.File]::WriteAllText((Join-Path $temporary 'plan.md'), 'two')
    & git -C $temporary add plan.md
    & git -C $temporary commit -m amendment | Out-Null
    $commit = Assert-RalphAmendmentCommit $temporary $base @('requirements.md','plan.md')
    Assert-TestTrue ($commit.Head -match '^[0-9a-f]{40}$') 'one authorized amendment commit accepted'
    [void](Invoke-RalphNative git @('-C',$temporary,'switch','-C','unauthorized',$base))
    [IO.File]::WriteAllText((Join-Path $temporary 'source.txt'), 'unauthorized')
    & git -C $temporary add source.txt
    & git -C $temporary commit -m 'unauthorized amendment' | Out-Null
    Assert-TestThrows { Assert-RalphAmendmentCommit $temporary $base @('requirements.md','plan.md') } 'unauthorized path'
    [void](Invoke-RalphNative git @('-C',$temporary,'switch','-C','main',$commit.Head))
    [IO.File]::WriteAllText((Join-Path $temporary 'source.txt'), 'unauthorized')
    & git -C $temporary add source.txt
    & git -C $temporary commit -m unauthorized | Out-Null
    Assert-TestThrows { Assert-RalphAmendmentCommit $temporary $base @('requirements.md','plan.md') } 'exactly one commit'
} finally { Remove-TestDirectory $temporary }

Write-Host "PM validation tests passed: $script:RalphTestCount assertions"
