param(
    [string]$SourceRepository = 'https://github.com/Kmac907/worktree-ralph.git',
    [string]$ProjectName,
    [string]$ParentDirectory,
    [ValidateSet('github', 'azure_devops')][string]$Provider,
    [ValidateSet('private', 'internal', 'public')][string]$Visibility = 'private',
    [string]$GitHubOwner,
    [string]$AzureOrganization,
    [string]$AzureProject,
    [ValidateRange(1, 32)][int]$MaximumConcurrentBuilders = 3,
    [ValidateRange(1, 32)][int]$MaximumConcurrentFixers = 3,
    [string]$WorktreeRoot,
    [string]$GitUserName,
    [string]$GitUserEmail
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$utf8 = [System.Text.UTF8Encoding]::new($false, $true)

function Invoke-BootstrapNative {
    param([string]$Command, [string[]]$Arguments, [string]$WorkingDirectory = (Get-Location).Path, [int[]]$AllowedExitCodes = @(0))
    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) { throw "Required command is unavailable: $Command" }
    Push-Location -LiteralPath $WorkingDirectory
    try {
        $lines = @(& $Command @Arguments 2>&1 | ForEach-Object {
            if ($null -eq $_) { '' } else { $_.ToString() }
        })
        $exitCode = $LASTEXITCODE
    } finally { Pop-Location }
    if ($exitCode -notin $AllowedExitCodes) { throw "Command failed with exit code ${exitCode}: $Command $($Arguments -join ' ')`n$($lines -join [Environment]::NewLine)" }
    [pscustomobject]@{ ExitCode = $exitCode; Output = ($lines -join [Environment]::NewLine); Lines = @($lines) }
}

function Read-RequiredValue {
    param([string]$Current, [string]$Prompt)
    if (-not [string]::IsNullOrWhiteSpace($Current)) { return $Current.Trim() }
    $value = Read-Host $Prompt
    if ([string]::IsNullOrWhiteSpace($value)) { throw "$Prompt is required." }
    $value.Trim()
}

$ProjectName = Read-RequiredValue -Current $ProjectName -Prompt 'Project name'
if ($ProjectName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$') { throw 'Project name contains unsupported characters.' }
$ParentDirectory = Read-RequiredValue -Current $ParentDirectory -Prompt 'Local parent directory'
if ([string]::IsNullOrWhiteSpace($Provider)) {
    $Provider = (Read-Host 'Provider (github or azure_devops)').Trim().ToLowerInvariant()
}
if ($Provider -notin @('github', 'azure_devops')) { throw 'Provider must be github or azure_devops.' }

foreach ($command in @('pwsh', 'git', 'codex')) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { throw "Required command is unavailable: $command" }
}

$target = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetFullPath($ParentDirectory)) $ProjectName))
if ([System.IO.Directory]::Exists($target) -and @(Get-ChildItem -LiteralPath $target -Force).Count -gt 0) {
    throw "Destination is not empty: $target"
}
if (-not [System.IO.Directory]::Exists($target)) { [void][System.IO.Directory]::CreateDirectory($target) }

$repositoryIdentity = $null
if ($Provider -ceq 'github') {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw 'GitHub bootstrap requires gh.' }
    [void](Invoke-BootstrapNative -Command 'gh' -Arguments @('auth', 'status'))
    if ([string]::IsNullOrWhiteSpace($GitHubOwner)) {
        $GitHubOwner = (Invoke-BootstrapNative -Command 'gh' -Arguments @('api', 'user', '--jq', '.login')).Output.Trim()
    }
    $repositoryIdentity = "$GitHubOwner/$ProjectName"
    $existing = Invoke-BootstrapNative -Command 'gh' -Arguments @('repo', 'view', $repositoryIdentity, '--json', 'nameWithOwner') -AllowedExitCodes @(0, 1)
    if ($existing.ExitCode -eq 0) { throw "GitHub repository already exists: $repositoryIdentity" }
} else {
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) { throw 'Azure DevOps bootstrap requires az.' }
    [void](Invoke-BootstrapNative -Command 'az' -Arguments @('account', 'show', '--output', 'none'))
    [void](Invoke-BootstrapNative -Command 'az' -Arguments @('extension', 'show', '--name', 'azure-devops', '--output', 'none'))
    $AzureOrganization = Read-RequiredValue -Current $AzureOrganization -Prompt 'Azure DevOps organization URL'
    $AzureProject = Read-RequiredValue -Current $AzureProject -Prompt 'Azure DevOps project'
    $repositoryIdentity = "$AzureOrganization|$AzureProject|$ProjectName"
    $existing = Invoke-BootstrapNative -Command 'az' -Arguments @('repos', 'show', '--organization', $AzureOrganization, '--project', $AzureProject, '--repository', $ProjectName, '--output', 'none') -AllowedExitCodes @(0, 1)
    if ($existing.ExitCode -eq 0) { throw "Azure DevOps repository already exists: $ProjectName" }
}

$temporaryRoot = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) ("worktree-ralph-bootstrap-{0}" -f [Guid]::NewGuid().ToString('N'))))
$sourceClone = Join-Path $temporaryRoot 'source'
$bootstrapSucceeded = $false
[void][System.IO.Directory]::CreateDirectory($temporaryRoot)

try {
    [void](Invoke-BootstrapNative -Command 'git' -Arguments @('clone', '--depth', '1', '--', $SourceRepository, $sourceClone))
    $template = Join-Path $sourceClone 'template'
    if (-not [System.IO.Directory]::Exists($template)) { throw "Source repository has no template directory: $SourceRepository" }
    foreach ($entry in Get-ChildItem -LiteralPath $template -Force) {
        Copy-Item -LiteralPath $entry.FullName -Destination $target -Recurse -Force
    }

    $workflowPath = Join-Path $target '.codex\workflow.json'
    $workflow = [System.IO.File]::ReadAllText($workflowPath, $utf8) | ConvertFrom-Json -Depth 50
    $workflow.provider = $Provider
    $workflow.maximumConcurrentBuilders = $MaximumConcurrentBuilders
    $workflow.maximumConcurrentFixers = $MaximumConcurrentFixers
    $workflow.worktreeRoot = if ([string]::IsNullOrWhiteSpace($WorktreeRoot)) { $null } else { [System.IO.Path]::GetFullPath($WorktreeRoot) }
    if ($Provider -ceq 'github') {
        $workflow.github.repository = $repositoryIdentity
    } else {
        $workflow.azureDevOps.organization = $AzureOrganization
        $workflow.azureDevOps.project = $AzureProject
        $workflow.azureDevOps.repository = $ProjectName
    }
    [System.IO.File]::WriteAllText($workflowPath, ($workflow | ConvertTo-Json -Depth 50), $utf8)

    [void](Invoke-BootstrapNative -Command 'git' -Arguments @('init', '-b', 'main') -WorkingDirectory $target)
    if (-not [string]::IsNullOrWhiteSpace($GitUserName)) {
        [void](Invoke-BootstrapNative -Command 'git' -Arguments @('config', 'user.name', $GitUserName.Trim()) -WorkingDirectory $target)
    }
    if (-not [string]::IsNullOrWhiteSpace($GitUserEmail)) {
        [void](Invoke-BootstrapNative -Command 'git' -Arguments @('config', 'user.email', $GitUserEmail.Trim()) -WorkingDirectory $target)
    }
    $userName = (Invoke-BootstrapNative -Command 'git' -Arguments @('config', 'user.name') -WorkingDirectory $target -AllowedExitCodes @(0, 1)).Output.Trim()
    $userEmail = (Invoke-BootstrapNative -Command 'git' -Arguments @('config', 'user.email') -WorkingDirectory $target -AllowedExitCodes @(0, 1)).Output.Trim()
    if ([string]::IsNullOrWhiteSpace($userName) -or [string]::IsNullOrWhiteSpace($userEmail)) { throw 'Git user.name and user.email must be configured before bootstrap.' }
    [void](Invoke-BootstrapNative -Command 'git' -Arguments @('add', '--all') -WorkingDirectory $target)
    [void](Invoke-BootstrapNative -Command 'git' -Arguments @('commit', '-m', 'Initialize Worktree Ralph project') -WorkingDirectory $target)

    if ($Provider -ceq 'github') {
        [void](Invoke-BootstrapNative -Command 'gh' -Arguments @('repo', 'create', $repositoryIdentity, "--$Visibility", '--source', $target, '--remote', 'origin', '--push') -WorkingDirectory $target)
    } else {
        $createdJson = (Invoke-BootstrapNative -Command 'az' -Arguments @('repos', 'create', '--organization', $AzureOrganization, '--project', $AzureProject, '--name', $ProjectName, '--output', 'json')).Output
        $created = $createdJson | ConvertFrom-Json -Depth 20
        if ([string]::IsNullOrWhiteSpace([string]$created.remoteUrl)) { throw 'Azure DevOps did not return a repository remote URL.' }
        [void](Invoke-BootstrapNative -Command 'git' -Arguments @('remote', 'add', 'origin', [string]$created.remoteUrl) -WorkingDirectory $target)
        [void](Invoke-BootstrapNative -Command 'git' -Arguments @('push', '--set-upstream', 'origin', 'main') -WorkingDirectory $target)
    }

    [void](Invoke-BootstrapNative -Command 'git' -Arguments @('fetch', 'origin') -WorkingDirectory $target)
    $localSha = (Invoke-BootstrapNative -Command 'git' -Arguments @('rev-parse', 'HEAD') -WorkingDirectory $target).Output.Trim()
    $remoteSha = (Invoke-BootstrapNative -Command 'git' -Arguments @('rev-parse', 'origin/main') -WorkingDirectory $target).Output.Trim()
    if ($localSha -cne $remoteSha) { throw 'Remote main does not match the initial local commit.' }

    $scriptErrors = [System.Collections.Generic.List[string]]::new()
    foreach ($script in Get-ChildItem -LiteralPath (Join-Path $target '.codex\scripts') -Filter '*.ps1') {
        $tokens = $null
        $errors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($script.FullName, [ref]$tokens, [ref]$errors)
        foreach ($parseError in @($errors)) { $scriptErrors.Add("$($script.Name): $($parseError.Message)") }
    }
    if ($scriptErrors.Count -gt 0) { throw "Copied workflow scripts are invalid: $($scriptErrors -join '; ')" }

    . (Join-Path $target '.codex\scripts\common.ps1')
    $configuration = Get-RalphConfiguration -RepositoryRoot $target
    $paths = Initialize-RalphStateFiles -RepositoryRoot $target -Configuration $configuration
    $state = Read-RalphJson -Path $paths.State -SchemaPath (Join-Path $paths.Schemas 'state.schema.json')
    if ([string]$state.repository -cne $repositoryIdentity) { throw 'Initialized state does not match the created remote repository.' }

    $bootstrapSucceeded = $true
    Write-Host ''
    Write-Host 'WORKTREE RALPH PROJECT CREATED'
    Write-Host "PROJECT:      $target"
    Write-Host "REMOTE:       $repositoryIdentity"
    Write-Host "INITIAL SHA:  $localSha"
    Write-Host 'NEXT:         Complete requirements.md, then run .codex/scripts/planning-loop.ps1.'
}
finally {
    if ($bootstrapSucceeded -and [System.IO.Directory]::Exists($temporaryRoot)) {
        $tempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        $relative = [System.IO.Path]::GetRelativePath($tempBase, $temporaryRoot)
        if ($relative -match '^worktree-ralph-bootstrap-[0-9a-f]{32}([\\/]|$)') {
            Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction Stop
        } else {
            throw "Refusing to remove unexpected temporary directory: $temporaryRoot"
        }
    } elseif (-not $bootstrapSucceeded) {
        Write-Warning "Bootstrap failed. The destination was preserved: $target"
        Write-Warning "Temporary source clone was preserved for diagnosis: $temporaryRoot"
    }
}
