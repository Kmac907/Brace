. (Join-Path $PSScriptRoot 'TestSupport.ps1')
$common = Join-Path (Split-Path $PSScriptRoot -Parent) 'template\.codex\scripts\common.ps1'
. $common

Assert-TestTrue -Condition (Test-RalphSafeRelativePattern 'src/**') -Message 'safe relative glob accepted'
Assert-TestTrue -Condition (-not (Test-RalphSafeRelativePattern '../secret')) -Message 'parent traversal rejected'
Assert-TestTrue -Condition (-not (Test-RalphSafeRelativePattern 'C:/secret')) -Message 'absolute path rejected'

$task1 = [pscustomobject]@{ taskId='TASK-0001'; dependencies=@(); allowedPaths=@('src/a/**'); exclusiveResources=@(); requirementIds=@('REQ-FUNC-001') }
$task2 = [pscustomobject]@{ taskId='TASK-0002'; dependencies=@(); allowedPaths=@('src/b/**'); exclusiveResources=@(); requirementIds=@('REQ-FUNC-002') }
$task3 = [pscustomobject]@{ taskId='TASK-0003'; dependencies=@('TASK-0001'); allowedPaths=@('src/c/**'); exclusiveResources=@(); requirementIds=@('REQ-FUNC-003') }
Assert-RalphGraph -Items @($task1,$task2,$task3) -Kind task
$task1 | Add-Member -NotePropertyName status -NotePropertyValue integrated
$task2 | Add-Member -NotePropertyName status -NotePropertyValue pending
$task3 | Add-Member -NotePropertyName status -NotePropertyValue pending
$ready = @(Select-RalphReadyItems -Items @($task1,$task2,$task3) -Kind task -Maximum 3)
Assert-TestEqual -Expected 2 -Actual $ready.Count -Message 'dependency-ready tasks selected'

$cycle1 = [pscustomobject]@{ taskId='TASK-0001'; dependencies=@('TASK-0002'); allowedPaths=@('a/**'); exclusiveResources=@(); requirementIds=@('REQ-A-001') }
$cycle2 = [pscustomobject]@{ taskId='TASK-0002'; dependencies=@('TASK-0001'); allowedPaths=@('b/**'); exclusiveResources=@(); requirementIds=@('REQ-B-001') }
Assert-TestThrows -Action { Assert-RalphGraph -Items @($cycle1,$cycle2) -Kind task } -Pattern 'cycle'

$unsafe = [pscustomobject]@{ taskId='TASK-0001'; dependencies=@(); allowedPaths=@('**'); exclusiveResources=@(); requirementIds=@('REQ-A-001') }
Assert-TestThrows -Action { Assert-RalphAssignmentPaths -Item $unsafe } -Pattern 'too broad'
Assert-TestThrows -Action { Assert-RalphTaskCoverage -Tasks @($task1) -RequirementsMarkdown '- REQ-MISSING-001: required' } -Pattern 'REQ-MISSING-001'
Assert-TestTrue -Condition (Test-RalphSemanticBlocker ([pscustomobject]@{kind='scope_gap';requiresUserDecision=$true;scopeChangePossible=$true})) -Message 'semantic scope blocker selects PM'
Assert-TestTrue -Condition (-not (Test-RalphSemanticBlocker ([pscustomobject]@{kind='operational';requiresUserDecision=$false;scopeChangePossible=$false}))) -Message 'operational blocker does not select PM'

$templateRoot = Join-Path (Split-Path $common -Parent) '..'
$plannerPrompt = [System.IO.File]::ReadAllText((Join-Path $templateRoot 'prompts\planner.md'))
$pmPrompt = [System.IO.File]::ReadAllText((Join-Path $templateRoot 'prompts\project-manager.md'))
Assert-TestTrue -Condition $plannerPrompt.Contains('one agent in one bounded session') -Message 'planner requires single-session tasks'
Assert-TestTrue -Condition $plannerPrompt.Contains('executable UI or browser verification when the repository provides suitable tooling') -Message 'planner requires conditional UI verification'
Assert-TestTrue -Condition $pmPrompt.Contains('one agent in one bounded session') -Message 'PM follow-up tasks preserve sizing rule'
Assert-TestTrue -Condition $pmPrompt.Contains('executable UI or browser verification when the repository provides suitable tooling') -Message 'PM follow-up tasks preserve conditional UI verification'
$invalidPromptCharacters = @(
    Get-ChildItem -LiteralPath (Join-Path $templateRoot 'prompts') -Filter '*.md' |
        ForEach-Object {
            $promptPath = $_.FullName
            [System.IO.File]::ReadAllText($promptPath).ToCharArray() |
                Where-Object { [int]$_ -lt 32 -and $_ -notin @("`t", "`r", "`n") } |
                ForEach-Object { "$promptPath contains U+$('{0:X4}' -f [int]$_)" }
        }
)
Assert-TestEqual -Expected 0 -Actual $invalidPromptCharacters.Count -Message 'agent prompts contain no unexpected control characters'

$temporary = New-TestDirectory
try {
    $schema = Join-Path (Split-Path $common -Parent) '..\schemas\state.schema.json'
    $path = Join-Path $temporary 'state.json'
    $state = [ordered]@{
        schemaVersion='1.2';revision=0;repositoryRoot=$temporary;provider='github';repository='owner/repo';remote='origin';remoteUrl='https://github.com/owner/repo.git';targetBranch='main';targetBaseSha=$null;integrationBranch='ralph/integration';configurationHash=('sha256:' + ('0' * 64));taskDefinitionHash=$null;bugDefinitionHash=$null;stage='requirements';stageStatus='not_started';requirementsHash=$null;planHash=$null;integrationSha=$null;acceptedIntegrationShas=@();finalMergeSha=$null;blocker=$null;amendmentSequence=0;activeAmendment=$null;createdAt=[DateTimeOffset]::UtcNow.ToString('O');updatedAt=[DateTimeOffset]::UtcNow.ToString('O')
    }
    Write-RalphJsonAtomic -Path $path -Value $state -SchemaPath $schema
    $read = Read-RalphJson -Path $path -SchemaPath $schema
    Assert-TestEqual -Expected 'requirements' -Actual ([string]$read.stage) -Message 'atomic state round trip'
} finally { Remove-TestDirectory $temporary }

Write-Host "Common tests passed: $script:RalphTestCount assertions"
