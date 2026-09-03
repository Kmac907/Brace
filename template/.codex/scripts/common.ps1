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
        Assignments = Join-Path $codex 'assignments'
        Results = Join-Path $codex 'results'
        Logs = Join-Path $codex 'logs'
        Lock = Join-Path $codex 'workflow.lock'
        Prompts = Join-Path $codex 'prompts'
        Schemas = Join-Path $codex 'schemas'
    }
}

function Invoke-RalphNative {
    param([Parameter(Mandatory)][string]$Command,[string[]]$Arguments=@(),[string]$WorkingDirectory=(Get-Location).Path,[int[]]$AllowedExitCodes=@(0))
    $commandInfo=Get-Command $Command -ErrorAction SilentlyContinue;if($null-eq$commandInfo){throw "Required command is unavailable: $Command"}
    $psi=[Diagnostics.ProcessStartInfo]::new();$psi.UseShellExecute=$false;$psi.RedirectStandardOutput=$true;$psi.RedirectStandardError=$true;$psi.CreateNoWindow=$true;$psi.WorkingDirectory=$WorkingDirectory
    if($commandInfo.CommandType-eq'ExternalScript'){$psi.FileName=(Get-Command pwsh).Source;foreach($arg in @('-NoProfile','-NonInteractive','-File',$commandInfo.Source)){$psi.ArgumentList.Add($arg)}}
    elseif($commandInfo.Source.EndsWith('.cmd',[StringComparison]::OrdinalIgnoreCase)-or$commandInfo.Source.EndsWith('.bat',[StringComparison]::OrdinalIgnoreCase)){$psi.FileName=$env:ComSpec;foreach($arg in @('/d','/c',$commandInfo.Source)){$psi.ArgumentList.Add($arg)}}
    else{$psi.FileName=$commandInfo.Source}
    foreach($arg in $Arguments){$psi.ArgumentList.Add($arg)}
    $process=[Diagnostics.Process]::new();$process.StartInfo=$psi
    try{if(-not$process.Start()){throw "Unable to start command: $Command"};$stdout=$process.StandardOutput.ReadToEndAsync();$stderr=$process.StandardError.ReadToEndAsync();if(-not$process.WaitForExit(600000)){try{$process.Kill($true)}catch{};[void]$process.WaitForExit(10000);throw "Command exceeded the 10-minute deadline: $Command"};$exitCode=$process.ExitCode;$lines=@(($stdout.GetAwaiter().GetResult()+[Environment]::NewLine+$stderr.GetAwaiter().GetResult()).TrimEnd()-split'\r?\n')}finally{$process.Dispose()}
    if($exitCode-notin$AllowedExitCodes){throw "Command failed with exit code ${exitCode}: $Command $($Arguments-join' ')`n$($lines-join[Environment]::NewLine)"}
    [pscustomobject]@{ExitCode=$exitCode;Lines=@($lines);Output=($lines-join[Environment]::NewLine)}
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
    if ([string]$config.schemaVersion -cne '1.0') { throw "Unsupported workflow configuration version: $($config.schemaVersion)" }
    if ([string]$config.provider -notin @('github', 'azure_devops')) { throw "Unsupported provider: $($config.provider)" }
    foreach ($name in @('remote', 'targetBranch', 'integrationBranch')) {
        if ([string]::IsNullOrWhiteSpace([string]$config.$name)) { throw "workflow.json is missing $name." }
    }
    if ([string]$config.targetBranch -ceq [string]$config.integrationBranch) { throw 'Target and integration branches must differ.' }
    foreach ($name in @('maximumConcurrentBuilders', 'maximumConcurrentFixers', 'maximumTaskAttempts', 'maximumBugAttempts', 'maximumPlanningQuestionRounds')) {
        if ([int]$config.$name -lt 1 -or [int]$config.$name -gt 32) { throw "workflow.json field $name must be between 1 and 32." }
    }
    if ([int]$config.agentTimeoutMinutes -lt 1 -or [int]$config.agentTimeoutMinutes -gt 1440) { throw 'agentTimeoutMinutes must be between 1 and 1440.' }
    if ([int]$config.agentCleanupGraceSeconds -lt 1 -or [int]$config.agentCleanupGraceSeconds -gt 120) { throw 'agentCleanupGraceSeconds must be between 1 and 120.' }
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
    param([Parameter(Mandatory)][string]$RepositoryRoot, [Parameter(Mandatory)]$Configuration, [Parameter(Mandatory)][string]$RepositoryIdentity)

    $now = [DateTimeOffset]::UtcNow.ToString('O')
    $remoteUrl = (Invoke-RalphNative git @('-C', $RepositoryRoot, 'config', '--get', "remote.$($Configuration.remote).url" )).Output.Trim()
    [ordered]@{
        schemaVersion = '1.0'; revision = 0; repositoryRoot = [System.IO.Path]::GetFullPath($RepositoryRoot)
        provider = [string]$Configuration.provider; repository = $RepositoryIdentity; remote = [string]$Configuration.remote
        remoteUrl = $remoteUrl; targetBranch = [string]$Configuration.targetBranch; targetBaseSha = $null
        integrationBranch = [string]$Configuration.integrationBranch; configurationHash = Get-RalphFileHash (Get-RalphPaths $RepositoryRoot).Config
        taskDefinitionHash = $null; bugDefinitionHash = $null; stage = 'requirements'; stageStatus = 'not_started'
        requirementsHash = $null; planHash = $null; integrationSha = $null; finalMergeSha = $null; blocker = $null
        createdAt = $now; updatedAt = $now
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
    param([Parameter(Mandatory)][string]$RepositoryRoot, [Parameter(Mandatory)]$Configuration)

    $paths = Get-RalphPaths -RepositoryRoot $RepositoryRoot
    foreach ($directory in @($paths.Logs, $paths.Assignments, $paths.Results)) { [void][System.IO.Directory]::CreateDirectory($directory) }
    if (-not [System.IO.File]::Exists($paths.State)) {
        $identity = Get-RalphRepositoryIdentity -RepositoryRoot $RepositoryRoot -Configuration $Configuration
        Write-RalphJsonAtomic $paths.State (New-RalphState $RepositoryRoot $Configuration $identity) (Join-Path $paths.Schemas 'state.schema.json')
    }
    if (-not [System.IO.File]::Exists($paths.Tasks)) {
        Write-RalphJsonAtomic $paths.Tasks ([ordered]@{ schemaVersion='1.0'; revision=0; planHash=$null; definitionHash=$null; status='not_planned'; tasks=@() }) (Join-Path $paths.Schemas 'tasks.schema.json')
    }
    if (-not [System.IO.File]::Exists($paths.Bugs)) {
        Write-RalphJsonAtomic $paths.Bugs ([ordered]@{ schemaVersion='1.0'; revision=0; auditSha=$null; definitionHash=$null; status='not_audited'; bugs=@() }) (Join-Path $paths.Schemas 'bugs.schema.json')
    }
    $paths
}

function Assert-RalphStateIdentity {
    param([Parameter(Mandatory)]$State, [Parameter(Mandatory)][string]$RepositoryRoot, [Parameter(Mandatory)]$Configuration)

    if ([System.IO.Path]::GetFullPath([string]$State.repositoryRoot) -cne [System.IO.Path]::GetFullPath($RepositoryRoot)) { throw 'state.json belongs to another repository path.' }
    foreach ($field in @('provider', 'remote', 'targetBranch', 'integrationBranch')) {
        if ([string]$State.$field -cne [string]$Configuration.$field) { throw "state.json $field differs from workflow.json." }
    }
    $configuredIdentity = Get-RalphRepositoryIdentity $RepositoryRoot $Configuration
    if ([string]$State.repository -cne $configuredIdentity) { throw 'state.json repository identity differs from the configured provider repository.' }
    if ([string]$State.configurationHash -cne (Get-RalphFileHash (Get-RalphPaths $RepositoryRoot).Config)) { throw 'workflow.json changed after workflow creation.' }
    $remoteUrl = (Invoke-RalphNative git @('-C', $RepositoryRoot, 'config', '--get', "remote.$($Configuration.remote).url" )).Output.Trim()
    if ([string]$State.remoteUrl -cne $remoteUrl) { throw 'Git remote URL changed after workflow creation.' }
    $normalized = $remoteUrl.ToLowerInvariant() -replace '\\','/' -replace '\.git$',''
    if ([string]$Configuration.provider -ceq 'github' -and -not $normalized.EndsWith(([string]$configuredIdentity).ToLowerInvariant())) { throw 'Git remote URL does not match the GitHub repository identity.' }
    if ([string]$Configuration.provider -ceq 'azure_devops') {
        $repo = ([string]$Configuration.azureDevOps.repository).ToLowerInvariant()
        if (-not ($normalized.EndsWith("/$repo") -or $normalized.EndsWith("/_git/$repo"))) { throw 'Git remote URL does not match the Azure DevOps repository identity.' }
    }
}

function Get-RalphObjectHash {
    param([Parameter(Mandatory)]$Value)
    $json = $Value | ConvertTo-Json -Depth 100 -Compress
    'sha256:' + [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($script:RalphUtf8.GetBytes($json))).ToLowerInvariant()
}

function Get-RalphDefinitionHash {
    param([Parameter(Mandatory)][object[]]$Items, [Parameter(Mandatory)][ValidateSet('task','bug')][string]$Kind)
    $definitions = foreach ($item in $Items) {
        if ($Kind -ceq 'task') {
            [ordered]@{ taskId=[string]$item.taskId; title=[string]$item.title; description=[string]$item.description; requirementIds=@($item.requirementIds); planSections=@($item.planSections); dependencies=@($item.dependencies); allowedPaths=@($item.allowedPaths); exclusiveResources=@($item.exclusiveResources); acceptanceCriteria=@($item.acceptanceCriteria); checks=@($item.checks) }
        } else {
            [ordered]@{ bugId=[string]$item.bugId; title=[string]$item.title; severity=[string]$item.severity; category=[string]$item.category; requirementIds=@($item.requirementIds); description=[string]$item.description; evidence=[string]$item.evidence; actualBehavior=[string]$item.actualBehavior; requiredBehavior=[string]$item.requiredBehavior; impact=[string]$item.impact; requiredCorrection=[string]$item.requiredCorrection; acceptanceTest=[string]$item.acceptanceTest; dependencies=@($item.dependencies); allowedPaths=@($item.allowedPaths); exclusiveResources=@($item.exclusiveResources) }
        }
    }
    Get-RalphObjectHash @($definitions)
}

function Assert-RalphLedgerIdentity {
    param([Parameter(Mandatory)]$State, [Parameter(Mandatory)]$Ledger, [Parameter(Mandatory)][ValidateSet('task','bug')][string]$Kind)
    if ($Kind -ceq 'task') {
        if ([string]$Ledger.planHash -cne [string]$State.planHash) { throw 'tasks.json plan hash differs from state.json.' }
        $expected = [string]$State.taskDefinitionHash; $actual = Get-RalphDefinitionHash @($Ledger.tasks) task
    } else {
        $expected = [string]$State.bugDefinitionHash; $actual = Get-RalphDefinitionHash @($Ledger.bugs) bug
    }
    if ([string]$Ledger.definitionHash -cne $actual -or $expected -cne $actual) { throw "$Kind ledger definitions changed after they were frozen." }
}

function Assert-RalphTargetDrift {
    param([Parameter(Mandatory)][string]$RepositoryRoot, [Parameter(Mandatory)]$Configuration, [Parameter(Mandatory)]$State)
    [void](Invoke-RalphNative git @('-C', $RepositoryRoot, 'fetch', [string]$Configuration.remote, '--prune'))
    $actual = (Invoke-RalphNative git @('-C', $RepositoryRoot, 'rev-parse', "$($Configuration.remote)/$($Configuration.targetBranch)")).Output.Trim()
    if ($null -ne $State.targetBaseSha -and [string]$State.targetBaseSha -cne $actual) { throw 'Target branch advanced after planning. Reconcile it before continuing.' }
    $actual
}

function Get-RalphAttemptPath {
    param([Parameter(Mandatory)]$Paths, [Parameter(Mandatory)][ValidateSet('assignment','result')][string]$Kind, [Parameter(Mandatory)][string]$Identity, [Parameter(Mandatory)][int]$Attempt)
    $directory = if ($Kind -ceq 'assignment') { $Paths.Assignments } else { $Paths.Results }
    Join-Path $directory ("{0}-attempt-{1:D3}.json" -f $Identity, $Attempt)
}

function Write-RalphImmutableJson {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Value)
    $json = $Value | ConvertTo-Json -Depth 100
    if ([IO.File]::Exists($Path)) {
        if ((Read-RalphText $Path).Trim() -cne $json.Trim()) { throw "Immutable attempt record already exists with different content: $Path" }
        return
    }
    Write-RalphTextAtomic $Path $json
}

function Read-RalphAttemptResult {
    param([Parameter(Mandatory)]$Paths, [Parameter(Mandatory)][string]$Identity, [Parameter(Mandatory)][int]$Attempt)
    $path = Get-RalphAttemptPath $Paths result $Identity $Attempt
    if (-not [IO.File]::Exists($path)) { return $null }
    Read-RalphJson $path
}

function Reset-RalphCompletedWorkflow {
    param([Parameter(Mandatory)][string]$RepositoryRoot, [Parameter(Mandatory)]$Configuration, [Parameter(Mandatory)]$State)
    if ([string]$State.stage -cne 'complete' -or [string]$State.stageStatus -cne 'complete') { throw 'Only a completed workflow may be replaced.' }
    if ([string]$State.finalMergeSha -notmatch '^[0-9a-f]{40}$') { throw 'Completed workflow is missing its verified final merge SHA.' }
    $status = (Invoke-RalphNative git @('-C',$RepositoryRoot,'status','--porcelain','--untracked-files=all')).Output
    if (-not [string]::IsNullOrWhiteSpace($status)) { throw 'The repository must be clean before starting a new workflow.' }
    $base = Get-RalphWorktreeBase $RepositoryRoot $Configuration
    if ([IO.Directory]::Exists($base) -and @(Get-ChildItem $base -Force).Count -gt 0) { throw 'Owned worktrees remain; clean them before starting a new workflow.' }
    [void](Invoke-RalphNative git @('-C',$RepositoryRoot,'fetch',[string]$Configuration.remote,'--prune'))
    foreach ($ref in @("refs/heads/$($Configuration.integrationBranch)", "refs/remotes/$($Configuration.remote)/$($Configuration.integrationBranch)")) {
        if ((Invoke-RalphNative git @('-C',$RepositoryRoot,'show-ref','--verify','--quiet',$ref) -AllowedExitCodes @(0,1)).ExitCode -eq 0) { throw 'The previous integration branch still exists.' }
    }
    $paths = Get-RalphPaths $RepositoryRoot
    $archiveRoot = Join-Path $paths.Logs ("completed-{0}" -f ([string]$State.finalMergeSha).Substring(0,12))
    foreach ($directory in @($paths.Assignments,$paths.Results)) {
        if ([IO.Directory]::Exists($directory) -and @(Get-ChildItem $directory -Force).Count) {
            [void][IO.Directory]::CreateDirectory($archiveRoot)
            $destination = Join-Path $archiveRoot ([IO.Path]::GetFileName($directory))
            if ([IO.Directory]::Exists($destination)) { throw "Completed attempt archive already exists: $destination" }
            [IO.Directory]::Move($directory,$destination)
        }
    }
    foreach ($file in @($paths.State,$paths.Tasks,$paths.Bugs,$paths.PlanningSummary,$paths.BuildSummary,$paths.AuditSummary)) { if ([IO.File]::Exists($file)) { [IO.File]::Delete($file) } }
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
    param([Parameter(Mandatory)][string]$Prompt, [Parameter(Mandatory)][string]$WorkingDirectory, [Parameter(Mandatory)][string]$SchemaPath, [ValidateSet('read-only','workspace-write')][string]$Sandbox='read-only', [Parameter(Mandatory)][string]$LogDirectory, [string]$Identity='agent', [int]$TimeoutMinutes=90, [int]$CleanupGraceSeconds=10, [int]$TimeoutSeconds=0)

    $command = Get-Command codex -ErrorAction SilentlyContinue
    if ($null -eq $command) { throw 'Codex CLI is unavailable.' }
    [void][IO.Directory]::CreateDirectory($LogDirectory)
    $token=[Guid]::NewGuid().ToString('N'); $safeIdentity=$Identity-replace'[^A-Za-z0-9_.-]','_'
    $resultPath=Join-Path $LogDirectory "$safeIdentity-$token.result.json"; $logPath=Join-Path $LogDirectory "$safeIdentity-$token.log"
    $psi=[Diagnostics.ProcessStartInfo]::new(); $psi.UseShellExecute=$false; $psi.RedirectStandardInput=$true; $psi.RedirectStandardOutput=$true; $psi.RedirectStandardError=$true; $psi.CreateNoWindow=$true; $psi.WorkingDirectory=$WorkingDirectory
    if ($command.CommandType -eq 'ExternalScript' -or $command.Source.EndsWith('.ps1',[StringComparison]::OrdinalIgnoreCase)) {
        $psi.FileName=(Get-Command pwsh).Source; foreach($arg in @('-NoProfile','-NonInteractive','-File',$command.Source)) { $psi.ArgumentList.Add($arg) }
    } else { $psi.FileName=$command.Source }
    foreach($arg in @('exec','--ephemeral','--color','never','--sandbox',$Sandbox,'--output-schema',$SchemaPath,'--output-last-message',$resultPath,'-')) { $psi.ArgumentList.Add($arg) }
    $process=[Diagnostics.Process]::new(); $process.StartInfo=$psi; $stdout=$null; $stderr=$null
    try {
        if(-not $process.Start()) { throw 'Unable to start Codex.' }
        $stdout=$process.StandardOutput.ReadToEndAsync(); $stderr=$process.StandardError.ReadToEndAsync(); $process.StandardInput.Write($Prompt); $process.StandardInput.Close()
        $timeoutMs=if($TimeoutSeconds-gt0){$TimeoutSeconds*1000}else{[Math]::Min([int]::MaxValue,$TimeoutMinutes*60000)};if(-not $process.WaitForExit($timeoutMs)) {
            try { $process.Kill($true) } catch { }
            if(-not $process.WaitForExit($CleanupGraceSeconds*1000)) { throw 'Codex timed out and its process tree did not stop within cleanup grace.' }
            throw "Codex exceeded the $TimeoutMinutes-minute deadline; its process tree was terminated."
        }
        $output=$stdout.GetAwaiter().GetResult()+[Environment]::NewLine+$stderr.GetAwaiter().GetResult()
        if($output.Length -gt $script:RalphMaximumLogBytes) { $output=$output.Substring(0,$script:RalphMaximumLogBytes)+"`n[log truncated]" }
        Write-RalphTextAtomic $logPath $output
        if($process.ExitCode -ne 0) { throw "Codex exited with code $($process.ExitCode). Log: $logPath" }
    } finally { $process.Dispose() }
    if(-not [IO.File]::Exists($resultPath)) { throw "Codex did not create its final result. Log: $logPath" }
    if(([IO.FileInfo]$resultPath).Length -gt $script:RalphMaximumResultBytes) { throw "Codex result exceeded 1 MiB: $resultPath" }
    $json=Read-RalphText $resultPath; Test-RalphJsonDocument $json $SchemaPath; $json|ConvertFrom-Json -Depth 100
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
        if (([string]$leftPath).Replace('/**','').IndexOfAny([char[]]'*?[') -ge 0) { return $true }
        $leftBase = ([string]$leftPath).Replace('\', '/').Replace('/**', '').TrimEnd('*').TrimEnd('/')
        foreach ($rightPath in @($Right.allowedPaths)) {
            if (([string]$rightPath).Replace('/**','').IndexOfAny([char[]]'*?[') -ge 0) { return $true }
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
    param([Parameter(Mandatory)][string]$RepositoryRoot,[Parameter(Mandatory)]$Configuration,[Parameter(Mandatory)][string]$Identity,[Parameter(Mandatory)][string]$Branch,[Parameter(Mandatory)][string]$BaseReference,[string]$ExpectedHeadSha)
    if($Identity-notmatch'^(TASK|BUG)-[0-9]{4}$'){throw "Invalid worktree identity: $Identity"}; if($Branch-cne"worktree/$Identity"){throw "Unexpected branch for ${Identity}: $Branch"}
    $base=Get-RalphWorktreeBase $RepositoryRoot $Configuration; [void][IO.Directory]::CreateDirectory($base); $path=[IO.Path]::GetFullPath((Join-Path $base $Identity))
    if([IO.Directory]::Exists($path)) {
        $actualRoot=(Invoke-RalphNative git @('-C',$path,'rev-parse','--show-toplevel')).Output.Trim(); $actualBranch=(Invoke-RalphNative git @('-C',$path,'branch','--show-current')).Output.Trim()
        if([IO.Path]::GetFullPath($actualRoot)-cne$path-or$actualBranch-cne$Branch){throw "Existing worktree does not match ${Identity}: $path"}
        $status=(Invoke-RalphNative git @('-C',$path,'status','--porcelain','--untracked-files=all')).Output; if(-not[string]::IsNullOrWhiteSpace($status)){throw "Interrupted worktree contains uncommitted changes: $Identity"}
        $head=(Invoke-RalphNative git @('-C',$path,'rev-parse','HEAD')).Output.Trim(); $ancestor=(Invoke-RalphNative git @('-C',$path,'merge-base','--is-ancestor',$BaseReference,$head)-AllowedExitCodes @(0,1)).ExitCode-eq0
        if(-not$ancestor){throw "Existing worktree branch does not descend from its recorded base: $Identity"}; if($ExpectedHeadSha-and$head-cne$ExpectedHeadSha){throw "Existing worktree HEAD differs from its recorded result: $Identity"}; return $path
    }
    $branchExists=(Invoke-RalphNative git @('-C',$RepositoryRoot,'show-ref','--verify','--quiet',"refs/heads/$Branch")-AllowedExitCodes @(0,1)).ExitCode-eq0
    if($branchExists){[void](Invoke-RalphNative git @('-C',$RepositoryRoot,'worktree','add','--',$path,$Branch))}else{[void](Invoke-RalphNative git @('-C',$RepositoryRoot,'worktree','add','-b',$Branch,'--',$path,$BaseReference))}
    $head=(Invoke-RalphNative git @('-C',$path,'rev-parse','HEAD')).Output.Trim(); if($branchExists-and(Invoke-RalphNative git @('-C',$path,'merge-base','--is-ancestor',$BaseReference,$head)-AllowedExitCodes @(0,1)).ExitCode-ne0){throw "Existing branch does not descend from its recorded base: $Identity"}; $path
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
    param([Parameter(Mandatory)][string]$RepositoryRoot,[Parameter(Mandatory)]$Configuration,[Parameter(Mandatory)]$State,[string[]]$AllowedMergeShas=@())
    [void](Invoke-RalphNative git @('-C',$RepositoryRoot,'fetch',[string]$Configuration.remote,'--prune')); $branch=[string]$Configuration.integrationBranch; $remote=[string]$Configuration.remote; $target=[string]$Configuration.targetBranch
    $localExists=(Invoke-RalphNative git @('-C',$RepositoryRoot,'show-ref','--verify','--quiet',"refs/heads/$branch")-AllowedExitCodes @(0,1)).ExitCode-eq0; $remoteExists=(Invoke-RalphNative git @('-C',$RepositoryRoot,'show-ref','--verify','--quiet',"refs/remotes/$remote/$branch")-AllowedExitCodes @(0,1)).ExitCode-eq0
    if(-not$localExists-and-not$remoteExists){[void](Invoke-RalphNative git @('-C',$RepositoryRoot,'branch',$branch,"$remote/$target"));[void](Invoke-RalphNative git @('-C',$RepositoryRoot,'push','--set-upstream',$remote,$branch))}elseif(-not$localExists){[void](Invoke-RalphNative git @('-C',$RepositoryRoot,'branch','--track',$branch,"$remote/$branch"))}elseif(-not$remoteExists){throw "Local integration branch exists without its remote counterpart: $branch"}
    $localSha=(Invoke-RalphNative git @('-C',$RepositoryRoot,'rev-parse',$branch)).Output.Trim(); $remoteSha=(Invoke-RalphNative git @('-C',$RepositoryRoot,'rev-parse',"$remote/$branch")).Output.Trim()
    foreach($from in @($localSha,[string]$State.integrationSha)|Where-Object{$_-and$_-cne$remoteSha}) {
        if((Invoke-RalphNative git @('-C',$RepositoryRoot,'merge-base','--is-ancestor',$from,$remoteSha)-AllowedExitCodes @(0,1)).ExitCode-ne0){throw "Integration branch diverged from recorded state: $branch"}
        $unknown=@((Invoke-RalphNative git @('-C',$RepositoryRoot,'rev-list','--first-parent',"$from..$remoteSha")).Lines|Where-Object{$_-notin$AllowedMergeShas}); if($unknown){throw "Integration branch contains unowned commits: $($unknown-join', ')"}
    }
    if($localSha-cne$remoteSha){[void](Invoke-RalphNative git @('-C',$RepositoryRoot,'branch','-f',$branch,$remoteSha))}; $remoteSha
}

function ConvertTo-RalphPullRequestRecord {
    param($PullRequest,[ValidateSet('github','azure_devops')][string]$Provider,[string]$Repository)
    if($Provider-eq'github'){[ordered]@{id=[string]$PullRequest.number;url=[string]$PullRequest.url;state=([string]$PullRequest.state).ToLowerInvariant();repository=$Repository;head=[string]$PullRequest.headRefName;headSha=[string]$PullRequest.headRefOid;base=[string]$PullRequest.baseRefName;baseSha=[string]$PullRequest.baseRefOid;mergeSha=if($PullRequest.mergeCommit){[string]$PullRequest.mergeCommit.oid}else{$null}}}
    else{[ordered]@{id=[string]$PullRequest.pullRequestId;url=[string]$PullRequest.url;state=([string]$PullRequest.status).ToLowerInvariant();repository=$Repository;head=([string]$PullRequest.sourceRefName)-replace'^refs/heads/','';headSha=[string]$PullRequest.lastMergeSourceCommit.commitId;base=([string]$PullRequest.targetRefName)-replace'^refs/heads/','';baseSha=[string]$PullRequest.lastMergeTargetCommit.commitId;mergeSha=if($PullRequest.lastMergeCommit){[string]$PullRequest.lastMergeCommit.commitId}else{$null}}}
}

function Get-RalphPullRequest {
    param([string]$RepositoryRoot,$Configuration,[string]$Head,[string]$Base,[string]$ExpectedHeadSha,[string]$PullRequestId)
    $repository=Get-RalphRepositoryIdentity $RepositoryRoot $Configuration
    if([string]$Configuration.provider-eq'github'){$json=(Invoke-RalphNative gh @('pr','list','--repo',$repository,'--state','all','--head',$Head,'--base',$Base,'--limit','50','--json','number,url,state,headRefName,headRefOid,baseRefName,baseRefOid,mergeCommit') $RepositoryRoot).Output;$raw=@($json|ConvertFrom-Json -Depth 20)}
    else{$s=$Configuration.azureDevOps;$json=(Invoke-RalphNative az @('repos','pr','list','--organization',[string]$s.organization,'--project',[string]$s.project,'--repository',[string]$s.repository,'--source-branch',$Head,'--target-branch',$Base,'--status','all','--output','json') $RepositoryRoot).Output;$raw=@($json|ConvertFrom-Json -Depth 20)}
    $records=@($raw|ForEach-Object{ConvertTo-RalphPullRequestRecord $_ ([string]$Configuration.provider) $repository}); if($PullRequestId){$records=@($records|Where-Object id -eq $PullRequestId)}
    if($ExpectedHeadSha){$exact=@($records|Where-Object headSha -eq $ExpectedHeadSha);if(-not$exact-and@($records|Where-Object state -in @('open','active')).Count){throw "An open pull request for $Head has a different head SHA."};$records=$exact}
    if($records.Count-gt1){throw "Multiple pull requests match exact assignment $Head -> $Base."};if(-not$records){return $null};$records[0]
}

function New-RalphPullRequest {
    param([string]$RepositoryRoot,$Configuration,[string]$Head,[string]$Base,[string]$ExpectedHeadSha,[string]$ExpectedBaseSha,[string]$Title,[string]$Body)
    $existing=Get-RalphPullRequest $RepositoryRoot $Configuration $Head $Base $ExpectedHeadSha;if($existing){return $existing}
    [void](Invoke-RalphNative git @('-C',$RepositoryRoot,'fetch',[string]$Configuration.remote,'--prune'));$remoteHead=(Invoke-RalphNative git @('-C',$RepositoryRoot,'rev-parse',"$($Configuration.remote)/$Head")).Output.Trim();$remoteBase=(Invoke-RalphNative git @('-C',$RepositoryRoot,'rev-parse',"$($Configuration.remote)/$Base")).Output.Trim()
    if($remoteHead-cne$ExpectedHeadSha-or$remoteBase-cne$ExpectedBaseSha){throw 'Remote pull-request head or base moved before creation.'}
    if([string]$Configuration.provider-eq'github'){$repo=Get-RalphRepositoryIdentity $RepositoryRoot $Configuration;[void](Invoke-RalphNative gh @('pr','create','--repo',$repo,'--head',$Head,'--base',$Base,'--title',$Title,'--body',$Body) $RepositoryRoot)}else{$s=$Configuration.azureDevOps;[void](Invoke-RalphNative az @('repos','pr','create','--organization',[string]$s.organization,'--project',[string]$s.project,'--repository',[string]$s.repository,'--source-branch',$Head,'--target-branch',$Base,'--title',$Title,'--description',$Body,'--output','none') $RepositoryRoot)}
    $created=Get-RalphPullRequest $RepositoryRoot $Configuration $Head $Base $ExpectedHeadSha;if(-not$created){throw 'Provider did not return the newly created exact pull request.'};$created.baseSha=$ExpectedBaseSha;$created
}

function Complete-RalphPullRequest {
    param([string]$RepositoryRoot,$Configuration,$PullRequest)
    if([string]$PullRequest.repository-cne(Get-RalphRepositoryIdentity $RepositoryRoot $Configuration)){throw 'Pull request repository identity does not match this workflow.'}
    if([string]$PullRequest.state-notin@('merged','completed')){if([string]$PullRequest.state-notin@('open','active')){throw "Pull request $($PullRequest.id) cannot be merged from state $($PullRequest.state)."};if([string]$Configuration.provider-eq'github'){$args=@('pr','merge',[string]$PullRequest.id,'--repo',[string]$PullRequest.repository,'--squash');if($Configuration.deleteMergedBranches){$args+='--delete-branch'};[void](Invoke-RalphNative gh $args $RepositoryRoot)}else{$s=$Configuration.azureDevOps;$args=@('repos','pr','update','--organization',[string]$s.organization,'--id',[string]$PullRequest.id,'--status','completed','--squash','true','--output','none');if($Configuration.deleteMergedBranches){$args+=@('--delete-source-branch','true')};[void](Invoke-RalphNative az $args $RepositoryRoot)}}
    $merged=Get-RalphPullRequest $RepositoryRoot $Configuration ([string]$PullRequest.head) ([string]$PullRequest.base) ([string]$PullRequest.headSha) ([string]$PullRequest.id);if(-not$merged-or[string]$merged.state-notin@('merged','completed')-or-not$merged.mergeSha){throw 'Provider did not return a verifiable merged pull request.'}
    [void](Invoke-RalphNative git @('-C',$RepositoryRoot,'fetch',[string]$Configuration.remote,'--prune'));$base=(Invoke-RalphNative git @('-C',$RepositoryRoot,'rev-parse',"$($Configuration.remote)/$($PullRequest.base)")).Output.Trim();if((Invoke-RalphNative git @('-C',$RepositoryRoot,'merge-base','--is-ancestor',[string]$merged.mergeSha,$base)-AllowedExitCodes @(0,1)).ExitCode-ne0){throw 'Remote base does not contain the provider merge result.'};$merged.baseSha=[string]$PullRequest.baseSha;$merged
}

function Publish-RalphAssignment {
    param([string]$RepositoryRoot,[string]$Worktree,$Configuration,$Item,[ValidateSet('task','bug')][string]$Kind)
    $identity=if($Kind-eq'task'){$Item.taskId}else{$Item.bugId};$branch=[string]$Item.branch;[void](Invoke-RalphNative git @('-C',$Worktree,'push','--set-upstream',[string]$Configuration.remote,$branch));[void](Invoke-RalphNative git @('-C',$RepositoryRoot,'fetch',[string]$Configuration.remote,'--prune'));$baseSha=(Invoke-RalphNative git @('-C',$RepositoryRoot,'rev-parse',"$($Configuration.remote)/$($Configuration.integrationBranch)")).Output.Trim();$body="Worktree Ralph $Kind $identity";$pr=New-RalphPullRequest $RepositoryRoot $Configuration $branch ([string]$Configuration.integrationBranch) ([string]$Item.resultSha) $baseSha "$identity $($Item.title)" $body;Complete-RalphPullRequest $RepositoryRoot $Configuration $pr
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
    param([Parameter(Mandatory)][string]$RepositoryRoot,[Parameter(Mandatory)][string]$WorkingDirectory,[Parameter(Mandatory)][string]$Role,[Parameter(Mandatory)][string]$Context,[Parameter(Mandatory)][string]$SchemaName,[ValidateSet('read-only','workspace-write')][string]$Sandbox)
    $paths=Get-RalphPaths $RepositoryRoot;$configuration=Get-RalphConfiguration $RepositoryRoot;$prompt=(Read-RalphText (Join-Path $paths.Codex 'AGENTS.md'))+"`n`n"+(Read-RalphText (Join-Path $paths.Prompts "$Role.md"))+"`n`n# Assignment context`n`n"+$Context
    Invoke-RalphCodex $prompt $WorkingDirectory (Join-Path $paths.Schemas $SchemaName) $Sandbox $paths.Logs $Role ([int]$configuration.agentTimeoutMinutes) ([int]$configuration.agentCleanupGraceSeconds)
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
