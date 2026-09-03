Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:RalphUtf8 = [System.Text.UTF8Encoding]::new($false, $true)
$script:RalphMaximumResultBytes = 1MB
$script:RalphMaximumLogBytes = 2MB

function Get-RalphRepositoryRoot {
    param([string]$Path = (Get-Location).Path)

    $result = Invoke-RalphNative -Command 'git' -Arguments @('-C', $Path, 'rev-parse', '--show-toplevel')
    $root = $result.Output.Trim()
    if ([string]::IsNullOrWhiteSpace($root)) {
        throw "Unable to determine the Git repository root from $Path."
    }
    [System.IO.Path]::GetFullPath($root)
}

function Get-RalphPaths {
    param([Parameter(Mandatory)][string]$RepositoryRoot)

    $root = [System.IO.Path]::GetFullPath($RepositoryRoot)
    $codex = Join-Path $root '.codex'
    [pscustomobject]@{
        RepositoryRoot = $root
        Codex = $codex
        Config = Join-Path $codex 'workflow.json'
        State = Join-Path $codex 'state.json'
        Tasks = Join-Path $codex 'tasks.json'
        Bugs = Join-Path $codex 'bugs.json'
        PlanningSummary = Join-Path $codex 'planning-summary.json'
        BuildSummary = Join-Path $codex 'build-summary.json'
        AuditSummary = Join-Path $codex 'audit-summary.json'
        Logs = Join-Path $codex 'logs'
        Lock = Join-Path $codex 'workflow.lock'
        Prompts = Join-Path $codex 'prompts'
        Schemas = Join-Path $codex 'schemas'
    }
}

function Invoke-RalphNative {
    param(
        [Parameter(Mandatory)][string]$Command,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = (Get-Location).Path,
        [int[]]$AllowedExitCodes = @(0)
    )

    $commandInfo = Get-Command $Command -ErrorAction SilentlyContinue
    if ($null -eq $commandInfo) {
        throw "Required command is unavailable: $Command"
    }

    Push-Location -LiteralPath $WorkingDirectory
    try {
        $lines = @(
            & $Command @Arguments 2>&1 |
                ForEach-Object { if ($null -eq $_) { '' } else { $_.ToString() } }
        )
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }

    if ($exitCode -notin $AllowedExitCodes) {
        $display = @($Command) + @($Arguments)
        throw "Command failed with exit code ${exitCode}: $($display -join ' ')`n$($lines -join [Environment]::NewLine)"
    }

    [pscustomobject]@{
        ExitCode = $exitCode
        Lines = @($lines)
        Output = ($lines -join [Environment]::NewLine)
    }
}

function Read-RalphText {
    param([Parameter(Mandatory)][string]$Path)

    if (-not [System.IO.File]::Exists($Path)) {
        throw "Required file does not exist: $Path"
    }
    [System.IO.File]::ReadAllText($Path, $script:RalphUtf8)
}

function Write-RalphTextAtomic {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Text
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    [void][System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($fullPath))
    $temporaryPath = "$fullPath.tmp.$([Guid]::NewGuid().ToString('N'))"
    try {
        [System.IO.File]::WriteAllText($temporaryPath, $Text, $script:RalphUtf8)
        [System.IO.File]::Move($temporaryPath, $fullPath, $true)
    }
    finally {
        if ([System.IO.File]::Exists($temporaryPath)) {
            [System.IO.File]::Delete($temporaryPath)
        }
    }
}

function Test-RalphJsonDocument {
    param(
        [Parameter(Mandatory)][string]$Json,
        [Parameter(Mandatory)][string]$SchemaPath
    )

    if (-not [System.IO.File]::Exists($SchemaPath)) {
        throw "JSON schema does not exist: $SchemaPath"
    }
    if (-not (Test-Json -Json $Json -SchemaFile $SchemaPath -ErrorAction Stop)) {
        throw "JSON does not satisfy schema $SchemaPath."
    }
}

function Read-RalphJson {
    param(
        [Parameter(Mandatory)][string]$Path,
        [string]$SchemaPath
    )

    $json = Read-RalphText -Path $Path
    if (-not [string]::IsNullOrWhiteSpace($SchemaPath)) {
        Test-RalphJsonDocument -Json $json -SchemaPath $SchemaPath
    }
    $json | ConvertFrom-Json -Depth 100
}

function Write-RalphJsonAtomic {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)]$Value,
        [Parameter(Mandatory)][string]$SchemaPath
    )

    $json = $Value | ConvertTo-Json -Depth 100
    Test-RalphJsonDocument -Json $json -SchemaPath $SchemaPath
    Write-RalphTextAtomic -Path $Path -Text $json
}

function Get-RalphFileHash {
    param([Parameter(Mandatory)][string]$Path)

    if (-not [System.IO.File]::Exists($Path)) {
        return $null
    }
    'sha256:' + (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Enter-RalphWorkflowLock {
    param([Parameter(Mandatory)][string]$Path)

    [void][System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($Path))
    try {
        $stream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
        $stream.SetLength(0)
        $bytes = $script:RalphUtf8.GetBytes("pid=$PID`nstarted=$([DateTimeOffset]::UtcNow.ToString('O'))")
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
        $stream
    }
    catch {
        throw "Another Worktree Ralph script is already running for this repository. Lock: $Path"
    }
}

function Get-RalphConfiguration {
    param([Parameter(Mandatory)][string]$RepositoryRoot)

    $paths = Get-RalphPaths -RepositoryRoot $RepositoryRoot
    $config = Read-RalphJson -Path $paths.Config
    if ([string]$config.schemaVersion -cne '1.0') {
        throw "Unsupported workflow configuration version: $($config.schemaVersion)"
    }
    if ([string]$config.provider -notin @('github', 'azure_devops')) {
        throw "Unsupported provider: $($config.provider)"
    }
    foreach ($name in @('remote', 'targetBranch', 'integrationBranch')) {
        if ([string]::IsNullOrWhiteSpace([string]$config.$name)) {
            throw "workflow.json is missing $name."
        }
    }
    foreach ($name in @('maximumConcurrentBuilders', 'maximumConcurrentFixers', 'maximumTaskAttempts', 'maximumBugAttempts', 'maximumPlanningQuestionRounds')) {
        if ([int]$config.$name -lt 1 -or [int]$config.$name -gt 32) {
            throw "workflow.json field $name must be between 1 and 32."
        }
    }
    $config
}

function Assert-RalphPrerequisites {
    param(
        [Parameter(Mandatory)]$Configuration,
        [switch]$RequireCodex
    )

    foreach ($command in @('git', 'pwsh')) {
        if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
            throw "Required command is unavailable: $command"
        }
    }
    if ($RequireCodex -and -not (Get-Command codex -ErrorAction SilentlyContinue)) {
        throw 'Required command is unavailable: codex'
    }
    if ([string]$Configuration.provider -ceq 'github') {
        if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
            throw 'GitHub provider requires the gh CLI.'
        }
        [void](Invoke-RalphNative -Command 'gh' -Arguments @('auth', 'status'))
    }
    else {
        if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
            throw 'Azure DevOps provider requires the az CLI.'
        }
        [void](Invoke-RalphNative -Command 'az' -Arguments @('account', 'show', '--output', 'none'))
        [void](Invoke-RalphNative -Command 'az' -Arguments @('extension', 'show', '--name', 'azure-devops', '--output', 'none'))
    }
}

function New-RalphState {
    param(
        [Parameter(Mandatory)][string]$RepositoryRoot,
        [Parameter(Mandatory)]$Configuration,
        [Parameter(Mandatory)][string]$RepositoryIdentity
    )

    $now = [DateTimeOffset]::UtcNow.ToString('O')
    [ordered]@{
        schemaVersion = '1.0'
        revision = 0
        repositoryRoot = [System.IO.Path]::GetFullPath($RepositoryRoot)
        provider = [string]$Configuration.provider
        repository = $RepositoryIdentity
        remote = [string]$Configuration.remote
        targetBranch = [string]$Configuration.targetBranch
        integrationBranch = [string]$Configuration.integrationBranch
        stage = 'requirements'
        stageStatus = 'not_started'
        requirementsHash = $null
        planHash = $null
        integrationSha = $null
        finalMergeSha = $null
        blocker = $null
        createdAt = $now
        updatedAt = $now
    }
}

function Save-RalphState {
    param(
        [Parameter(Mandatory)]$State,
        [Parameter(Mandatory)]$Paths
    )

    $State.revision = [int]$State.revision + 1
    $State.updatedAt = [DateTimeOffset]::UtcNow.ToString('O')
    Write-RalphJsonAtomic -Path $Paths.State -Value $State -SchemaPath (Join-Path $Paths.Schemas 'state.schema.json')
}

function Set-RalphBlocked {
    param(
        [Parameter(Mandatory)]$State,
        [Parameter(Mandatory)]$Paths,
        [Parameter(Mandatory)][string]$Scope,
        [string]$Identity,
        [Parameter(Mandatory)][string]$Message,
        [Parameter(Mandatory)][string]$RequiredDecision
    )

    $State.stage = 'blocked'
    $State.stageStatus = 'blocked'
    $State.blocker = [ordered]@{
        scope = $Scope
        identity = if ([string]::IsNullOrWhiteSpace($Identity)) { $null } else { $Identity }
        message = $Message
        requiredDecision = $RequiredDecision
    }
    Save-RalphState -State $State -Paths $Paths
}

function Get-RalphRepositoryIdentity {
    param(
        [Parameter(Mandatory)][string]$RepositoryRoot,
        [Parameter(Mandatory)]$Configuration
    )

    if ([string]$Configuration.provider -ceq 'github') {
        if (-not [string]::IsNullOrWhiteSpace([string]$Configuration.github.repository)) {
            return [string]$Configuration.github.repository
        }
        return (Invoke-RalphNative -Command 'gh' -Arguments @('repo', 'view', '--json', 'nameWithOwner', '--jq', '.nameWithOwner') -WorkingDirectory $RepositoryRoot).Output.Trim()
    }

    foreach ($field in @('organization', 'project', 'repository')) {
        if ([string]::IsNullOrWhiteSpace([string]$Configuration.azureDevOps.$field)) {
            throw "Azure DevOps configuration is missing $field."
        }
    }
    "$($Configuration.azureDevOps.organization)|$($Configuration.azureDevOps.project)|$($Configuration.azureDevOps.repository)"
}

function Initialize-RalphStateFiles {
    param(
        [Parameter(Mandatory)][string]$RepositoryRoot,
        [Parameter(Mandatory)]$Configuration
    )

    $paths = Get-RalphPaths -RepositoryRoot $RepositoryRoot
    [void][System.IO.Directory]::CreateDirectory($paths.Logs)
    if (-not [System.IO.File]::Exists($paths.State)) {
        $identity = Get-RalphRepositoryIdentity -RepositoryRoot $RepositoryRoot -Configuration $Configuration
        $state = New-RalphState -RepositoryRoot $RepositoryRoot -Configuration $Configuration -RepositoryIdentity $identity
        Write-RalphJsonAtomic -Path $paths.State -Value $state -SchemaPath (Join-Path $paths.Schemas 'state.schema.json')
    }
    if (-not [System.IO.File]::Exists($paths.Tasks)) {
        $tasks = [ordered]@{ schemaVersion = '1.0'; revision = 0; planHash = $null; status = 'not_planned'; tasks = @() }
        Write-RalphJsonAtomic -Path $paths.Tasks -Value $tasks -SchemaPath (Join-Path $paths.Schemas 'tasks.schema.json')
    }
    if (-not [System.IO.File]::Exists($paths.Bugs)) {
        $bugs = [ordered]@{ schemaVersion = '1.0'; revision = 0; auditSha = $null; status = 'not_audited'; bugs = @() }
        Write-RalphJsonAtomic -Path $paths.Bugs -Value $bugs -SchemaPath (Join-Path $paths.Schemas 'bugs.schema.json')
    }
    $paths
}

function Assert-RalphStateIdentity {
    param(
        [Parameter(Mandatory)]$State,
        [Parameter(Mandatory)][string]$RepositoryRoot,
        [Parameter(Mandatory)]$Configuration
    )

    if ([System.IO.Path]::GetFullPath([string]$State.repositoryRoot) -cne [System.IO.Path]::GetFullPath($RepositoryRoot)) {
        throw 'state.json belongs to another repository path.'
    }
    foreach ($field in @('provider', 'remote', 'targetBranch', 'integrationBranch')) {
        if ([string]$State.$field -cne [string]$Configuration.$field) {
            throw "state.json $field differs from workflow.json."
        }
    }
}

function Assert-RalphPlanDrift {
    param(
        [Parameter(Mandatory)]$State,
        [Parameter(Mandatory)][string]$RepositoryRoot,
        [switch]$RequirePlan
    )

    $requirementsPath = Join-Path $RepositoryRoot 'requirements.md'
    $planPath = Join-Path $RepositoryRoot 'plan.md'
    $requirementsHash = Get-RalphFileHash -Path $requirementsPath
    $planHash = Get-RalphFileHash -Path $planPath
    if ($RequirePlan -and $null -eq $planHash) {
        throw 'plan.md is required. Run the planning loop first.'
    }
    if ($null -ne $State.requirementsHash -and $State.requirementsHash -cne $requirementsHash) {
        throw 'requirements.md changed after planning. Reconcile the change before continuing.'
    }
    if ($RequirePlan -and $null -ne $State.planHash -and $State.planHash -cne $planHash) {
        throw 'plan.md changed after task generation. Reconcile the change before continuing.'
    }
}

function Invoke-RalphCodex {
    param(
        [Parameter(Mandatory)][string]$Prompt,
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][string]$SchemaPath,
        [ValidateSet('read-only', 'workspace-write')][string]$Sandbox = 'read-only',
        [Parameter(Mandatory)][string]$LogDirectory,
        [string]$Identity = 'agent'
    )

    if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
        throw 'Codex CLI is unavailable.'
    }
    [void][System.IO.Directory]::CreateDirectory($LogDirectory)
    $token = [Guid]::NewGuid().ToString('N')
    $safeIdentity = $Identity -replace '[^A-Za-z0-9_.-]', '_'
    $resultPath = Join-Path $LogDirectory "$safeIdentity-$token.result.json"
    $logPath = Join-Path $LogDirectory "$safeIdentity-$token.log"
    $buffer = [System.Text.StringBuilder]::new()
    $truncated = $false
    Push-Location -LiteralPath $WorkingDirectory
    try {
        $Prompt |
            & codex exec --ephemeral --color never --sandbox $Sandbox --output-schema $SchemaPath --output-last-message $resultPath - 2>&1 |
            ForEach-Object {
                $line = if ($null -eq $_) { '' } else { $_.ToString() }
                Write-Host $line
                if ($buffer.Length -lt $script:RalphMaximumLogBytes) {
                    $remaining = $script:RalphMaximumLogBytes - $buffer.Length
                    if ($line.Length + 2 -le $remaining) {
                        [void]$buffer.AppendLine($line)
                    }
                    else {
                        [void]$buffer.AppendLine($line.Substring(0, [Math]::Max(0, $remaining - 2)))
                        $truncated = $true
                    }
                }
                else {
                    $truncated = $true
                }
            }
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    if ($truncated) {
        [void]$buffer.AppendLine('[log truncated]')
    }
    Write-RalphTextAtomic -Path $logPath -Text $buffer.ToString()
    if ($exitCode -ne 0) {
        throw "Codex exited with code $exitCode. Log: $logPath"
    }
    if (-not [System.IO.File]::Exists($resultPath)) {
        throw "Codex did not create its final result. Log: $logPath"
    }
    $info = [System.IO.FileInfo]::new($resultPath)
    if ($info.Length -gt $script:RalphMaximumResultBytes) {
        throw "Codex result exceeded 1 MiB: $resultPath"
    }
    $json = Read-RalphText -Path $resultPath
    Test-RalphJsonDocument -Json $json -SchemaPath $SchemaPath
    $json | ConvertFrom-Json -Depth 100
}

function Test-RalphSafeRelativePattern {
    param([Parameter(Mandatory)][string]$Pattern)

    if ([string]::IsNullOrWhiteSpace($Pattern) -or [System.IO.Path]::IsPathRooted($Pattern)) { return $false }
    $normalized = $Pattern.Replace('\', '/').Trim()
    if ($normalized.StartsWith('/', [StringComparison]::Ordinal) -or $normalized -match '(^|/)[.][.](/|$)') { return $false }
    if ($normalized -match '(^|/)[.]git(/|$)') { return $false }
    $true
}

function Assert-RalphAssignmentPaths {
    param([Parameter(Mandatory)]$Item)

    $identity = if ($null -ne $Item.PSObject.Properties['taskId']) { [string]$Item.taskId } else { [string]$Item.bugId }
    $protected = @(
        'requirements.md', 'plan.md', '.codex/state.json', '.codex/tasks.json',
        '.codex/bugs.json', '.codex/planning-summary.json', '.codex/build-summary.json',
        '.codex/audit-summary.json', '.codex/logs', '.codex/logs/**'
    )
    foreach ($pattern in @($Item.allowedPaths)) {
        if (-not (Test-RalphSafeRelativePattern -Pattern ([string]$pattern))) {
            throw "$identity contains an unsafe allowed path: $pattern"
        }
        $normalized = ([string]$pattern).Replace('\', '/').TrimStart('.', '/')
        $patternBase = $normalized.Replace('/**', '').TrimEnd('*').TrimEnd('/')
        if ([string]::IsNullOrWhiteSpace($patternBase)) {
            throw "Assignment path is too broad to protect coordinator state: $pattern"
        }
        foreach ($blocked in $protected) {
            $blockedBase = $blocked.Replace('/**', '')
            if ($normalized -ceq $blocked -or $patternBase -ceq $blockedBase -or $blockedBase.StartsWith("$patternBase/", [StringComparison]::Ordinal)) {
                throw "Assignment path may include coordinator-owned content: $pattern"
            }
        }
    }
}

function Assert-RalphGraph {
    param(
        [Parameter(Mandatory)][object[]]$Items,
        [Parameter(Mandatory)][ValidateSet('task', 'bug')][string]$Kind
    )

    $idProperty = if ($Kind -ceq 'task') { 'taskId' } else { 'bugId' }
    $prefix = if ($Kind -ceq 'task') { 'TASK' } else { 'BUG' }
    $byId = @{}
    for ($index = 0; $index -lt $Items.Count; $index += 1) {
        $item = $Items[$index]
        $expected = '{0}-{1:D4}' -f $prefix, ($index + 1)
        $identity = [string]$item.$idProperty
        if ($identity -cne $expected) { throw "Expected $Kind identity $expected but found $identity." }
        if ($byId.ContainsKey($identity)) { throw "Duplicate $Kind identity: $identity" }
        $byId[$identity] = $item
        Assert-RalphAssignmentPaths -Item $item
    }
    foreach ($item in $Items) {
        $identity = [string]$item.$idProperty
        foreach ($dependency in @($item.dependencies)) {
            if (-not $byId.ContainsKey([string]$dependency)) { throw "$identity depends on unknown $Kind $dependency." }
            if ([string]$dependency -ceq $identity) { throw "$identity depends on itself." }
        }
    }
    $visiting = @{}
    $visited = @{}
    $visit = {
        param([string]$Identity)
        if ($visiting.ContainsKey($Identity)) { throw "$Kind dependency graph contains a cycle at $Identity." }
        if ($visited.ContainsKey($Identity)) { return }
        $visiting[$Identity] = $true
        foreach ($dependency in @($byId[$Identity].dependencies)) { & $visit ([string]$dependency) }
        [void]$visiting.Remove($Identity)
        $visited[$Identity] = $true
    }
    foreach ($identity in $byId.Keys) { & $visit $identity }
}

function Assert-RalphTaskCoverage {
    param(
        [Parameter(Mandatory)][object[]]$Tasks,
        [Parameter(Mandatory)][string]$RequirementsMarkdown,
        [string[]]$DeferredRequirementIds = @()
    )

    $requiredIds = @(
        [regex]::Matches($RequirementsMarkdown, '(?m)\bREQ-[A-Z0-9-]+\b') |
            ForEach-Object { $_.Value } |
            Where-Object { $_ -notmatch '^REQ-NONGOAL-' -and $_ -notin $DeferredRequirementIds } |
            Sort-Object -Unique
    )
    $coveredIds = @($Tasks | ForEach-Object { @($_.requirementIds) } | ForEach-Object { [string]$_ } | Sort-Object -Unique)
    $missing = @($requiredIds | Where-Object { $_ -notin $coveredIds })
    if ($missing.Count -gt 0) { throw "Task graph does not cover requirements: $($missing -join ', ')" }
}

function Test-RalphItemsConflict {
    param([Parameter(Mandatory)]$Left, [Parameter(Mandatory)]$Right)

    foreach ($resource in @($Left.exclusiveResources)) {
        if ([string]$resource -in @($Right.exclusiveResources | ForEach-Object { [string]$_ })) { return $true }
    }
    foreach ($leftPath in @($Left.allowedPaths)) {
        $leftBase = ([string]$leftPath).Replace('\', '/').Replace('/**', '').TrimEnd('*').TrimEnd('/')
        foreach ($rightPath in @($Right.allowedPaths)) {
            $rightBase = ([string]$rightPath).Replace('\', '/').Replace('/**', '').TrimEnd('*').TrimEnd('/')
            if ($leftBase -ceq $rightBase -or $leftBase.StartsWith("$rightBase/", [StringComparison]::Ordinal) -or $rightBase.StartsWith("$leftBase/", [StringComparison]::Ordinal)) { return $true }
        }
    }
    $false
}

function Select-RalphReadyItems {
    param(
        [Parameter(Mandatory)][object[]]$Items,
        [Parameter(Mandatory)][ValidateSet('task', 'bug')][string]$Kind,
        [Parameter(Mandatory)][int]$Maximum
    )

    $idProperty = if ($Kind -ceq 'task') { 'taskId' } else { 'bugId' }
    $pendingStatus = if ($Kind -ceq 'task') { 'pending' } else { 'open' }
    $completeStatus = if ($Kind -ceq 'task') { 'integrated' } else { 'verified' }
    $byId = @{}
    foreach ($item in $Items) { $byId[[string]$item.$idProperty] = $item }
    $ready = @($Items | Where-Object {
        [string]$_.status -ceq $pendingStatus -and
        @($_.dependencies | Where-Object { [string]$byId[[string]$_].status -cne $completeStatus }).Count -eq 0
    })
    $selected = [System.Collections.Generic.List[object]]::new()
    foreach ($candidate in $ready) {
        if ($selected.Count -ge $Maximum) { break }
        if (@($selected | Where-Object { Test-RalphItemsConflict -Left $_ -Right $candidate }).Count -eq 0) { $selected.Add($candidate) }
    }
    @($selected)
}
function Get-RalphWorktreeBase {
    param([Parameter(Mandatory)][string]$RepositoryRoot, [Parameter(Mandatory)]$Configuration)

    $configured = [string]$Configuration.worktreeRoot
    $parent = if ([string]::IsNullOrWhiteSpace($configured)) {
        Join-Path ([System.IO.Path]::GetTempPath()) 'worktree-ralph'
    } else {
        [System.IO.Path]::GetFullPath($configured)
    }
    $sha = [System.Security.Cryptography.SHA256]::HashData($script:RalphUtf8.GetBytes([System.IO.Path]::GetFullPath($RepositoryRoot).ToUpperInvariant()))
    $repositoryId = ([BitConverter]::ToString($sha).Replace('-', '').Substring(0, 16)).ToLowerInvariant()
    [System.IO.Path]::GetFullPath((Join-Path $parent $repositoryId))
}

function Remove-RalphEmptyWorktreeContainers {
    param(
        [Parameter(Mandatory)][string]$RepositoryRoot,
        [Parameter(Mandatory)]$Configuration
    )

    $base = Get-RalphWorktreeBase -RepositoryRoot $RepositoryRoot -Configuration $Configuration
    if ([System.IO.Directory]::Exists($base) -and @(Get-ChildItem -LiteralPath $base -Force).Count -eq 0) {
        [System.IO.Directory]::Delete($base)
    }

    $configuredRoot = [string]$Configuration.worktreeRoot
    if (-not [string]::IsNullOrWhiteSpace($configuredRoot)) {
        $root = [System.IO.Path]::GetFullPath($configuredRoot)
        $relative = [System.IO.Path]::GetRelativePath($root, $base)
        if (
            -not [System.IO.Path]::IsPathRooted($relative) -and
            $relative -ne '..' -and
            -not $relative.StartsWith("..$([System.IO.Path]::DirectorySeparatorChar)", [StringComparison]::Ordinal) -and
            [System.IO.Directory]::Exists($root) -and
            @(Get-ChildItem -LiteralPath $root -Force).Count -eq 0
        ) {
            [System.IO.Directory]::Delete($root)
        }
    }
}

function New-RalphWorktree {
    param(
        [Parameter(Mandatory)][string]$RepositoryRoot,
        [Parameter(Mandatory)]$Configuration,
        [Parameter(Mandatory)][string]$Identity,
        [Parameter(Mandatory)][string]$Branch,
        [Parameter(Mandatory)][string]$BaseReference
    )

    if ($Identity -notmatch '^(TASK|BUG)-[0-9]{4}$') { throw "Invalid worktree identity: $Identity" }
    if ($Branch -cne "worktree/$Identity") { throw "Unexpected branch for ${Identity}: $Branch" }
    $base = Get-RalphWorktreeBase -RepositoryRoot $RepositoryRoot -Configuration $Configuration
    [void][System.IO.Directory]::CreateDirectory($base)
    $path = [System.IO.Path]::GetFullPath((Join-Path $base $Identity))
    if ([System.IO.Directory]::Exists($path)) {
        $actualRoot = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $path, 'rev-parse', '--show-toplevel')).Output.Trim()
        $actualBranch = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $path, 'branch', '--show-current')).Output.Trim()
        if ([System.IO.Path]::GetFullPath($actualRoot) -cne $path -or $actualBranch -cne $Branch) {
            throw "Existing worktree does not match ${Identity}: $path"
        }
        return $path
    }
    $branchExists = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'show-ref', '--verify', '--quiet', "refs/heads/$Branch") -AllowedExitCodes @(0, 1)).ExitCode -eq 0
    if ($branchExists) {
        [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'worktree', 'add', '--', $path, $Branch))
    } else {
        [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'worktree', 'add', '-b', $Branch, '--', $path, $BaseReference))
    }
    $path
}

function Remove-RalphWorktree {
    param(
        [Parameter(Mandatory)][string]$RepositoryRoot,
        [Parameter(Mandatory)]$Configuration,
        [Parameter(Mandatory)][string]$Identity,
        [Parameter(Mandatory)][string]$Branch
    )

    $base = Get-RalphWorktreeBase -RepositoryRoot $RepositoryRoot -Configuration $Configuration
    $path = [System.IO.Path]::GetFullPath((Join-Path $base $Identity))
    $relative = [System.IO.Path]::GetRelativePath($base, $path)
    if ([System.IO.Path]::IsPathRooted($relative) -or $relative -eq '..' -or $relative.StartsWith('../', [StringComparison]::Ordinal) -or $relative.StartsWith('..', [StringComparison]::Ordinal) -or [System.IO.Path]::GetFileName($path) -cne $Identity) {
        throw "Refusing to remove unexpected worktree path: $path"
    }
    if ([System.IO.Directory]::Exists($path)) {
        [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'worktree', 'remove', '--force', '--', $path))
    }
    $exists = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'show-ref', '--verify', '--quiet', "refs/heads/$Branch") -AllowedExitCodes @(0, 1)).ExitCode -eq 0
    if ($exists) {
        [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'branch', '-D', '--', $Branch))
    }
    Remove-RalphEmptyWorktreeContainers -RepositoryRoot $RepositoryRoot -Configuration $Configuration
}

function Assert-RalphAssignmentCommit {
    param([Parameter(Mandatory)][string]$Worktree, [Parameter(Mandatory)][string]$BaseSha, [Parameter(Mandatory)]$Item)

    $status = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $Worktree, 'status', '--porcelain', '--untracked-files=all')).Output
    if (-not [string]::IsNullOrWhiteSpace($status)) { throw 'Agent worktree is not clean after its reported commit.' }
    $head = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $Worktree, 'rev-parse', 'HEAD')).Output.Trim()
    $ancestor = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $Worktree, 'merge-base', '--is-ancestor', $BaseSha, $head) -AllowedExitCodes @(0, 1)).ExitCode -eq 0
    if (-not $ancestor -or $head -ceq $BaseSha) { throw 'Agent did not create a descendant commit for the assignment.' }
    $changed = @((Invoke-RalphNative -Command 'git' -Arguments @('-C', $Worktree, 'diff', '--name-only', "$BaseSha..$head")).Lines)
    foreach ($changedPath in $changed) {
        $normalized = ([string]$changedPath).Replace('\', '/')
        if (@($Item.allowedPaths | Where-Object { $normalized -like ([string]$_).Replace('\', '/') }).Count -eq 0) {
            throw "Agent modified a path outside its assignment: $normalized"
        }
        if ($normalized -in @('requirements.md', 'plan.md', '.codex/state.json', '.codex/tasks.json', '.codex/bugs.json')) {
            throw "Agent modified coordinator-owned content: $normalized"
        }
    }
    [pscustomobject]@{ Head = $head; ChangedFiles = @($changed) }
}

function Ensure-RalphIntegrationBranch {
    param(
        [Parameter(Mandatory)][string]$RepositoryRoot,
        [Parameter(Mandatory)]$Configuration,
        [Parameter(Mandatory)]$State
    )

    [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'fetch', [string]$Configuration.remote, '--prune'))
    $branch = [string]$Configuration.integrationBranch
    $remote = [string]$Configuration.remote
    $target = [string]$Configuration.targetBranch
    $localExists = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'show-ref', '--verify', '--quiet', "refs/heads/$branch") -AllowedExitCodes @(0, 1)).ExitCode -eq 0
    $remoteExists = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'show-ref', '--verify', '--quiet', "refs/remotes/$remote/$branch") -AllowedExitCodes @(0, 1)).ExitCode -eq 0
    if (-not $localExists -and -not $remoteExists) {
        [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'branch', $branch, "$remote/$target"))
        [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'push', '--set-upstream', $remote, $branch))
    } elseif (-not $localExists) {
        [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'branch', '--track', $branch, "$remote/$branch"))
    } elseif ($remoteExists) {
        $localSha = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'rev-parse', $branch)).Output.Trim()
        $remoteSha = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'rev-parse', "$remote/$branch")).Output.Trim()
        if ($localSha -cne $remoteSha) {
            $localBehind = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'merge-base', '--is-ancestor', $localSha, $remoteSha) -AllowedExitCodes @(0, 1)).ExitCode -eq 0
            if (-not $localBehind) { throw "Local and remote integration branches diverged: $branch" }
            [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'branch', '-f', $branch, $remoteSha))
        }
    } else {
        [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'push', '--set-upstream', $remote, $branch))
    }
    $sha = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'rev-parse', $branch)).Output.Trim()
    if ($null -ne $State.integrationSha -and [string]$State.integrationSha -cne $sha) {
        $recordedAncestor = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'merge-base', '--is-ancestor', [string]$State.integrationSha, $sha) -AllowedExitCodes @(0, 1)).ExitCode -eq 0
        if (-not $recordedAncestor) { throw 'Integration branch no longer descends from the recorded integration SHA.' }
    }
    $sha
}
function ConvertTo-RalphPullRequestRecord {
    param([Parameter(Mandatory)]$PullRequest, [Parameter(Mandatory)][ValidateSet('github', 'azure_devops')][string]$Provider)

    if ($Provider -ceq 'github') {
        [ordered]@{
            id = [string]$PullRequest.number
            url = [string]$PullRequest.url
            state = ([string]$PullRequest.state).ToLowerInvariant()
            head = [string]$PullRequest.headRefName
            base = [string]$PullRequest.baseRefName
            mergeSha = if ($null -ne $PullRequest.mergeCommit -and -not [string]::IsNullOrWhiteSpace([string]$PullRequest.mergeCommit.oid)) { [string]$PullRequest.mergeCommit.oid } else { $null }
        }
    } else {
        [ordered]@{
            id = [string]$PullRequest.pullRequestId
            url = [string]$PullRequest.url
            state = ([string]$PullRequest.status).ToLowerInvariant()
            head = ([string]$PullRequest.sourceRefName) -replace '^refs/heads/', ''
            base = ([string]$PullRequest.targetRefName) -replace '^refs/heads/', ''
            mergeSha = if ($null -ne $PullRequest.lastMergeCommit -and -not [string]::IsNullOrWhiteSpace([string]$PullRequest.lastMergeCommit.commitId)) { [string]$PullRequest.lastMergeCommit.commitId } else { $null }
        }
    }
}

function Get-RalphPullRequest {
    param(
        [Parameter(Mandatory)][string]$RepositoryRoot,
        [Parameter(Mandatory)]$Configuration,
        [Parameter(Mandatory)][string]$Head,
        [Parameter(Mandatory)][string]$Base
    )

    if ([string]$Configuration.provider -ceq 'github') {
        $repository = Get-RalphRepositoryIdentity -RepositoryRoot $RepositoryRoot -Configuration $Configuration
        $json = (Invoke-RalphNative -Command 'gh' -Arguments @(
            'pr', 'list', '--repo', $repository, '--state', 'all', '--head', $Head, '--base', $Base,
            '--limit', '20', '--json', 'number,url,state,headRefName,baseRefName,mergeCommit'
        ) -WorkingDirectory $RepositoryRoot).Output
        $items = @($json | ConvertFrom-Json -Depth 20)
        if ($items.Count -gt 1) { throw "Multiple pull requests match $Head -> $Base." }
        if ($items.Count -eq 0) { return $null }
        return ConvertTo-RalphPullRequestRecord -PullRequest $items[0] -Provider github
    }

    $settings = $Configuration.azureDevOps
    $json = (Invoke-RalphNative -Command 'az' -Arguments @(
        'repos', 'pr', 'list', '--organization', [string]$settings.organization,
        '--project', [string]$settings.project, '--repository', [string]$settings.repository,
        '--source-branch', $Head, '--target-branch', $Base, '--status', 'all', '--output', 'json'
    ) -WorkingDirectory $RepositoryRoot).Output
    $items = @($json | ConvertFrom-Json -Depth 20)
    if ($items.Count -gt 1) { throw "Multiple pull requests match $Head -> $Base." }
    if ($items.Count -eq 0) { return $null }
    ConvertTo-RalphPullRequestRecord -PullRequest $items[0] -Provider azure_devops
}

function New-RalphPullRequest {
    param(
        [Parameter(Mandatory)][string]$RepositoryRoot,
        [Parameter(Mandatory)]$Configuration,
        [Parameter(Mandatory)][string]$Head,
        [Parameter(Mandatory)][string]$Base,
        [Parameter(Mandatory)][string]$Title,
        [Parameter(Mandatory)][string]$Body
    )

    $existing = Get-RalphPullRequest -RepositoryRoot $RepositoryRoot -Configuration $Configuration -Head $Head -Base $Base
    if ($null -ne $existing) {
        if ([string]$existing.head -cne $Head -or [string]$existing.base -cne $Base) { throw 'Existing pull-request identity does not match the requested head and base.' }
        return $existing
    }
    if ([string]$Configuration.provider -ceq 'github') {
        $repository = Get-RalphRepositoryIdentity -RepositoryRoot $RepositoryRoot -Configuration $Configuration
        [void](Invoke-RalphNative -Command 'gh' -Arguments @('pr', 'create', '--repo', $repository, '--head', $Head, '--base', $Base, '--title', $Title, '--body', $Body) -WorkingDirectory $RepositoryRoot)
    } else {
        $settings = $Configuration.azureDevOps
        [void](Invoke-RalphNative -Command 'az' -Arguments @(
            'repos', 'pr', 'create', '--organization', [string]$settings.organization,
            '--project', [string]$settings.project, '--repository', [string]$settings.repository,
            '--source-branch', $Head, '--target-branch', $Base, '--title', $Title,
            '--description', $Body, '--output', 'none'
        ) -WorkingDirectory $RepositoryRoot)
    }
    $created = Get-RalphPullRequest -RepositoryRoot $RepositoryRoot -Configuration $Configuration -Head $Head -Base $Base
    if ($null -eq $created) { throw "Provider did not return the newly created pull request for $Head." }
    $created
}

function Complete-RalphPullRequest {
    param(
        [Parameter(Mandatory)][string]$RepositoryRoot,
        [Parameter(Mandatory)]$Configuration,
        [Parameter(Mandatory)]$PullRequest
    )

    if ([string]$PullRequest.state -in @('merged', 'completed')) { return $PullRequest }
    if ([string]$PullRequest.state -notin @('open', 'active')) { throw "Pull request $($PullRequest.id) cannot be merged from state $($PullRequest.state)." }
    if ([string]$Configuration.provider -ceq 'github') {
        $repository = Get-RalphRepositoryIdentity -RepositoryRoot $RepositoryRoot -Configuration $Configuration
        $arguments = @('pr', 'merge', [string]$PullRequest.id, '--repo', $repository, '--squash')
        if ([bool]$Configuration.deleteMergedBranches) { $arguments += '--delete-branch' }
        [void](Invoke-RalphNative -Command 'gh' -Arguments $arguments -WorkingDirectory $RepositoryRoot)
    } else {
        $settings = $Configuration.azureDevOps
        $arguments = @(
            'repos', 'pr', 'update', '--organization', [string]$settings.organization,
            '--id', [string]$PullRequest.id, '--status', 'completed', '--squash', 'true', '--output', 'none'
        )
        if ([bool]$Configuration.deleteMergedBranches) { $arguments += @('--delete-source-branch', 'true') }
        [void](Invoke-RalphNative -Command 'az' -Arguments $arguments -WorkingDirectory $RepositoryRoot)
    }
    $merged = Get-RalphPullRequest -RepositoryRoot $RepositoryRoot -Configuration $Configuration -Head ([string]$PullRequest.head) -Base ([string]$PullRequest.base)
    if ($null -eq $merged -or [string]$merged.state -notin @('merged', 'completed')) { throw "Provider did not report pull request $($PullRequest.id) as merged." }
    [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'fetch', [string]$Configuration.remote, '--prune'))
    $remoteBase = "$($Configuration.remote)/$($PullRequest.base)"
    $baseSha = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'rev-parse', $remoteBase)).Output.Trim()
    if ($null -ne $merged.mergeSha) {
        $containsMerge = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'merge-base', '--is-ancestor', [string]$merged.mergeSha, $baseSha) -AllowedExitCodes @(0, 1)).ExitCode -eq 0
        if (-not $containsMerge) { throw "Remote base does not contain provider merge result $($merged.mergeSha)." }
    } else {
        $merged.mergeSha = $baseSha
    }
    $merged
}

function Publish-RalphAssignment {
    param(
        [Parameter(Mandatory)][string]$RepositoryRoot,
        [Parameter(Mandatory)][string]$Worktree,
        [Parameter(Mandatory)]$Configuration,
        [Parameter(Mandatory)]$Item,
        [Parameter(Mandatory)][ValidateSet('task', 'bug')][string]$Kind
    )

    $identity = if ($Kind -ceq 'task') { [string]$Item.taskId } else { [string]$Item.bugId }
    $branch = [string]$Item.branch
    [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $Worktree, 'push', '--set-upstream', [string]$Configuration.remote, $branch))
    $newline = [Environment]::NewLine
    $body = if ($Kind -ceq 'task') {
        "Worktree Ralph implementation for $identity.$newline$newline" + 'Acceptance criteria:' + $newline + '- ' + (@($Item.acceptanceCriteria) -join "$newline- ")
    } else {
        "Worktree Ralph correction for $identity.$newline$newline" + 'Required correction:' + $newline + [string]$Item.requiredCorrection + "$newline$newline" + 'Acceptance test:' + $newline + [string]$Item.acceptanceTest
    }
    $pr = New-RalphPullRequest -RepositoryRoot $RepositoryRoot -Configuration $Configuration -Head $branch -Base ([string]$Configuration.integrationBranch) -Title "$identity $($Item.title)" -Body $body
    if ([string]$pr.head -cne $branch -or [string]$pr.base -cne [string]$Configuration.integrationBranch) { throw "Pull request for $identity has an unexpected head or base." }
    Complete-RalphPullRequest -RepositoryRoot $RepositoryRoot -Configuration $Configuration -PullRequest $pr
}

function Remove-RalphMergedAssignment {
    param(
        [Parameter(Mandatory)][string]$RepositoryRoot,
        [Parameter(Mandatory)]$Configuration,
        [Parameter(Mandatory)][string]$Identity,
        [Parameter(Mandatory)][string]$Branch,
        [Parameter(Mandatory)]$PullRequest
    )

    if ([string]$PullRequest.state -notin @('merged', 'completed')) { throw "Refusing cleanup because pull request $($PullRequest.id) is not merged." }
    Remove-RalphWorktree -RepositoryRoot $RepositoryRoot -Configuration $Configuration -Identity $Identity -Branch $Branch
    $remoteRef = "refs/remotes/$($Configuration.remote)/$Branch"
    $remoteExists = (Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'show-ref', '--verify', '--quiet', $remoteRef) -AllowedExitCodes @(0, 1)).ExitCode -eq 0
    if ($remoteExists -and [bool]$Configuration.deleteMergedBranches) {
        [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'push', [string]$Configuration.remote, '--delete', $Branch))
    }
}

function Invoke-RalphRole {
    param(
        [Parameter(Mandatory)][string]$RepositoryRoot,
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][string]$Role,
        [Parameter(Mandatory)][string]$Context,
        [Parameter(Mandatory)][string]$SchemaName,
        [ValidateSet('read-only', 'workspace-write')][string]$Sandbox
    )

    $paths = Get-RalphPaths -RepositoryRoot $RepositoryRoot
    $commonPrompt = Read-RalphText -Path (Join-Path $paths.Codex 'AGENTS.md')
    $rolePrompt = Read-RalphText -Path (Join-Path $paths.Prompts "$Role.md")
    $prompt = $commonPrompt + [Environment]::NewLine + [Environment]::NewLine + $rolePrompt + [Environment]::NewLine + [Environment]::NewLine + '# Assignment context' + [Environment]::NewLine + [Environment]::NewLine + $Context
    Invoke-RalphCodex -Prompt $prompt -WorkingDirectory $WorkingDirectory -SchemaPath (Join-Path $paths.Schemas $SchemaName) -Sandbox $Sandbox -LogDirectory $paths.Logs -Identity $Role
}

function Write-RalphSummary {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Summary)
    Write-RalphTextAtomic -Path $Path -Text ($Summary | ConvertTo-Json -Depth 100)
}

function Show-RalphStatus {
    param([Parameter(Mandatory)]$State, [object]$Tasks, [object]$Bugs)

    Write-Host ''
    Write-Host "STAGE:           $($State.stage)"
    Write-Host "STATUS:          $($State.stageStatus)"
    Write-Host "REPOSITORY:      $($State.repository)"
    Write-Host "TARGET:          $($State.targetBranch)"
    Write-Host "INTEGRATION:     $($State.integrationBranch)"
    Write-Host "INTEGRATION SHA: $($State.integrationSha)"
    if ($null -ne $Tasks) { Write-Host "TASKS:           $(@($Tasks.tasks | Where-Object status -eq 'integrated').Count)/$(@($Tasks.tasks).Count) integrated" }
    if ($null -ne $Bugs) { Write-Host "BUGS:            $(@($Bugs.bugs | Where-Object status -eq 'verified').Count)/$(@($Bugs.bugs).Count) verified" }
    if ($null -ne $State.blocker) {
        Write-Host "BLOCKER:         $($State.blocker.message)"
        Write-Host "DECISION:        $($State.blocker.requiredDecision)"
    }
}
function New-RalphAuditWorktree {
    param(
        [Parameter(Mandatory)][string]$RepositoryRoot,
        [Parameter(Mandatory)]$Configuration,
        [Parameter(Mandatory)][string]$Reference
    )

    $base = Get-RalphWorktreeBase -RepositoryRoot $RepositoryRoot -Configuration $Configuration
    [void][System.IO.Directory]::CreateDirectory($base)
    $path = [System.IO.Path]::GetFullPath((Join-Path $base 'AUDIT'))
    if ([System.IO.Directory]::Exists($path)) {
        [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'worktree', 'remove', '--force', '--', $path))
    }
    [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'worktree', 'add', '--detach', '--', $path, $Reference))
    $path
}

function Remove-RalphAuditWorktree {
    param([Parameter(Mandatory)][string]$RepositoryRoot, [Parameter(Mandatory)]$Configuration)

    $base = Get-RalphWorktreeBase -RepositoryRoot $RepositoryRoot -Configuration $Configuration
    $path = [System.IO.Path]::GetFullPath((Join-Path $base 'AUDIT'))
    $relative = [System.IO.Path]::GetRelativePath($base, $path)
    if ($relative -cne 'AUDIT') { throw "Refusing to remove unexpected audit worktree: $path" }
    if ([System.IO.Directory]::Exists($path)) {
        [void](Invoke-RalphNative -Command 'git' -Arguments @('-C', $RepositoryRoot, 'worktree', 'remove', '--force', '--', $path))
    }
    Remove-RalphEmptyWorktreeContainers -RepositoryRoot $RepositoryRoot -Configuration $Configuration
}
