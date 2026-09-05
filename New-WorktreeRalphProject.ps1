param(
    [string]$SourceRepository = 'https://github.com/Kmac907/worktree-ralph.git',
    [string]$ExistingRepositoryPath,
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

function Read-ExistingRepositoryChoice {
    $answer = (Read-Host 'Is this an existing Git repository? [y/N]').Trim().ToLowerInvariant()
    if ([string]::IsNullOrWhiteSpace($answer) -or $answer -in @('n', 'no')) { return $false }
    if ($answer -in @('y', 'yes')) { return $true }
    throw 'Answer y or n.'
}

function Add-BootstrapSection {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Content
    )

    $begin = "# BEGIN $Name"
    $end = "# END $Name"
    $existing = if ([System.IO.File]::Exists($Path)) {
        [System.IO.File]::ReadAllText($Path, $utf8)
    } else {
        ''
    }
    if ($existing.Contains($begin) -or $existing.Contains($end)) {
        throw "The existing file contains an incomplete or duplicate $Name section: $Path"
    }
    $prefix = if ([string]::IsNullOrWhiteSpace($existing)) { '' } else { $existing.TrimEnd() + "`n`n" }
    [System.IO.File]::WriteAllText($Path, "$prefix$begin`n$($Content.Trim())`n$end`n", $utf8)
}

function Get-ExistingRepositoryDetails {
    param([Parameter(Mandatory)][string]$Path)

    if (-not [System.IO.Directory]::Exists($Path)) {
        throw "Existing repository directory does not exist: $Path"
    }
    $requested = [System.IO.Path]::GetFullPath($Path)
    $rootResult = Invoke-BootstrapNative `
        -Command 'git' `
        -Arguments @('-C', $requested, 'rev-parse', '--show-toplevel') `
        -AllowedExitCodes @(0, 128)
    if ($rootResult.ExitCode -ne 0) { throw "Path is not a Git repository: $requested" }
    $root = [System.IO.Path]::GetFullPath($rootResult.Output.Trim())
    if ($root -cne $requested) { Write-Host "Using repository root: $root" }

    $status = (Invoke-BootstrapNative -Command 'git' -Arguments @('-C', $root, 'status', '--porcelain', '--untracked-files=all')).Output
    if (-not [string]::IsNullOrWhiteSpace($status)) {
        throw "Existing repository worktree is not clean: $root"
    }
    if ([System.IO.Directory]::Exists((Join-Path $root '.codex'))) {
        throw "Existing repository already contains .codex; refusing to overwrite or duplicate a workflow installation: $root"
    }

    $currentBranch = (Invoke-BootstrapNative -Command 'git' -Arguments @('-C', $root, 'branch', '--show-current')).Output.Trim()
    if ([string]::IsNullOrWhiteSpace($currentBranch)) {
        throw 'Existing repository must be checked out on a branch, not a detached HEAD.'
    }
    $remoteResult = Invoke-BootstrapNative `
        -Command 'git' `
        -Arguments @('-C', $root, 'config', '--get', 'remote.origin.url') `
        -AllowedExitCodes @(0, 1)
    if ($remoteResult.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($remoteResult.Output)) {
        throw 'Existing repository must have an origin remote.'
    }
    $remoteUrl = $remoteResult.Output.Trim()

    [void](Invoke-BootstrapNative -Command 'git' -Arguments @('-C', $root, 'fetch', 'origin', '--prune'))
    $remoteHead = Invoke-BootstrapNative `
        -Command 'git' `
        -Arguments @('-C', $root, 'symbolic-ref', '--quiet', '--short', 'refs/remotes/origin/HEAD') `
        -AllowedExitCodes @(0, 1)
    $targetBranch = if ($remoteHead.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($remoteHead.Output)) {
        $remoteHead.Output.Trim() -replace '^origin/', ''
    } else {
        $currentBranch
    }
    if ($currentBranch -cne $targetBranch) {
        throw "Existing repository must be checked out on its target branch '$targetBranch'; current branch is '$currentBranch'."
    }

    $localSha = (Invoke-BootstrapNative -Command 'git' -Arguments @('-C', $root, 'rev-parse', 'HEAD')).Output.Trim()
    $remoteShaResult = Invoke-BootstrapNative `
        -Command 'git' `
        -Arguments @('-C', $root, 'rev-parse', "refs/remotes/origin/$targetBranch") `
        -AllowedExitCodes @(0, 128)
    if ($remoteShaResult.ExitCode -ne 0) { throw "Origin does not contain target branch '$targetBranch'." }
    if ($localSha -cne $remoteShaResult.Output.Trim()) {
        throw "Local $targetBranch must exactly match origin/$targetBranch before installation."
    }

    $normalized = $remoteUrl.Trim() -replace '\.git$', ''
    if ($normalized -match '(?i)github\.com[:/](?<owner>[^/:\s]+)/(?<repository>[^/\s]+)$') {
        return [pscustomobject]@{
            Root = $root
            Provider = 'github'
            TargetBranch = $targetBranch
            RepositoryIdentity = "$($Matches.owner)/$($Matches.repository)"
            RepositoryName = $Matches.repository
            GitHubOwner = $Matches.owner
            AzureOrganization = $null
            AzureProject = $null
        }
    }
    if (
        $normalized -match '(?i)dev\.azure\.com/(?<organization>[^/]+)/(?<project>[^/]+)/_git/(?<repository>[^/]+)$' -or
        $normalized -match '(?i)dev\.azure\.com:v3/(?<organization>[^/]+)/(?<project>[^/]+)/(?<repository>[^/]+)$'
    ) {
        $organization = [Uri]::UnescapeDataString($Matches.organization)
        $project = [Uri]::UnescapeDataString($Matches.project)
        $repository = [Uri]::UnescapeDataString($Matches.repository)
        $organizationUrl = "https://dev.azure.com/$organization"
        return [pscustomobject]@{
            Root = $root
            Provider = 'azure_devops'
            TargetBranch = $targetBranch
            RepositoryIdentity = "$organizationUrl|$project|$repository"
            RepositoryName = $repository
            GitHubOwner = $null
            AzureOrganization = $organizationUrl
            AzureProject = $project
        }
    }
    throw "Unable to determine GitHub or Azure DevOps repository identity from origin: $remoteUrl"
}

$useExistingRepository = -not [string]::IsNullOrWhiteSpace($ExistingRepositoryPath)
if (
    $useExistingRepository -and
    (-not [string]::IsNullOrWhiteSpace($ProjectName) -or -not [string]::IsNullOrWhiteSpace($ParentDirectory))
) {
    throw 'ExistingRepositoryPath cannot be combined with ProjectName or ParentDirectory.'
}
if (
    -not $useExistingRepository -and
    [string]::IsNullOrWhiteSpace($ProjectName) -and
    [string]::IsNullOrWhiteSpace($ParentDirectory)
) {
    $useExistingRepository = Read-ExistingRepositoryChoice
    if ($useExistingRepository) {
        $enteredPath = Read-Host "Existing repository path [$((Get-Location).Path)]"
        $ExistingRepositoryPath = if ([string]::IsNullOrWhiteSpace($enteredPath)) {
            (Get-Location).Path
        } else {
            $enteredPath.Trim()
        }
    }
}

foreach ($command in @('pwsh', 'git', 'codex')) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { throw "Required command is unavailable: $command" }
}

$repositoryIdentity = $null
$targetBranch = 'main'
$target = $null
if ($useExistingRepository) {
    $details = Get-ExistingRepositoryDetails -Path $ExistingRepositoryPath
    $target = $details.Root
    $targetBranch = $details.TargetBranch
    if (-not [string]::IsNullOrWhiteSpace($Provider) -and $Provider -cne $details.Provider) {
        throw "Configured provider '$Provider' does not match the origin remote provider '$($details.Provider)'."
    }
    $Provider = $details.Provider
    $ProjectName = $details.RepositoryName
    $repositoryIdentity = $details.RepositoryIdentity
    if ($Provider -ceq 'github') {
        if (-not [string]::IsNullOrWhiteSpace($GitHubOwner) -and $GitHubOwner -cne $details.GitHubOwner) {
            throw 'GitHubOwner does not match origin.'
        }
        $GitHubOwner = $details.GitHubOwner
    } else {
        if (
            -not [string]::IsNullOrWhiteSpace($AzureOrganization) -and
            $AzureOrganization.TrimEnd('/') -cne $details.AzureOrganization
        ) {
            throw 'AzureOrganization does not match origin.'
        }
        if (-not [string]::IsNullOrWhiteSpace($AzureProject) -and $AzureProject -cne $details.AzureProject) {
            throw 'AzureProject does not match origin.'
        }
        $AzureOrganization = $details.AzureOrganization
        $AzureProject = $details.AzureProject
    }
} else {
    $ProjectName = Read-RequiredValue -Current $ProjectName -Prompt 'Project name'
    if ($ProjectName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$') {
        throw 'Project name contains unsupported characters.'
    }
    $ParentDirectory = Read-RequiredValue -Current $ParentDirectory -Prompt 'Local parent directory'
    if ([string]::IsNullOrWhiteSpace($Provider)) {
        $Provider = (Read-Host 'Provider (github or azure_devops)').Trim().ToLowerInvariant()
    }
    if ($Provider -notin @('github', 'azure_devops')) { throw 'Provider must be github or azure_devops.' }
    $target = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetFullPath($ParentDirectory)) $ProjectName))
    if ([System.IO.Directory]::Exists($target) -and @(Get-ChildItem -LiteralPath $target -Force).Count -gt 0) {
        throw "Destination is not empty: $target"
    }
    if (-not [System.IO.Directory]::Exists($target)) { [void][System.IO.Directory]::CreateDirectory($target) }
}

if ($Provider -ceq 'github') {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw 'GitHub bootstrap requires gh.' }
    [void](Invoke-BootstrapNative -Command 'gh' -Arguments @('auth', 'status'))
    if (-not $useExistingRepository) {
        if ([string]::IsNullOrWhiteSpace($GitHubOwner)) {
            $GitHubOwner = (Invoke-BootstrapNative -Command 'gh' -Arguments @('api', 'user', '--jq', '.login')).Output.Trim()
        }
        $repositoryIdentity = "$GitHubOwner/$ProjectName"
        $existing = Invoke-BootstrapNative -Command 'gh' -Arguments @('repo', 'view', $repositoryIdentity, '--json', 'nameWithOwner') -AllowedExitCodes @(0, 1)
        if ($existing.ExitCode -eq 0) { throw "GitHub repository already exists: $repositoryIdentity" }
    }
} else {
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) { throw 'Azure DevOps bootstrap requires az.' }
    [void](Invoke-BootstrapNative -Command 'az' -Arguments @('account', 'show', '--output', 'none'))
    [void](Invoke-BootstrapNative -Command 'az' -Arguments @('extension', 'show', '--name', 'azure-devops', '--output', 'none'))
    if (-not $useExistingRepository) {
        $AzureOrganization = Read-RequiredValue -Current $AzureOrganization -Prompt 'Azure DevOps organization URL'
        $AzureProject = Read-RequiredValue -Current $AzureProject -Prompt 'Azure DevOps project'
        $repositoryIdentity = "$AzureOrganization|$AzureProject|$ProjectName"
        $existing = Invoke-BootstrapNative -Command 'az' -Arguments @('repos', 'show', '--organization', $AzureOrganization, '--project', $AzureProject, '--repository', $ProjectName, '--output', 'none') -AllowedExitCodes @(0, 1)
        if ($existing.ExitCode -eq 0) { throw "Azure DevOps repository already exists: $ProjectName" }
    }
}

$temporaryRoot = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) ("worktree-ralph-bootstrap-{0}" -f [Guid]::NewGuid().ToString('N'))))
$sourceClone = Join-Path $temporaryRoot 'source'
$bootstrapSucceeded = $false
[void][System.IO.Directory]::CreateDirectory($temporaryRoot)

try {
    [void](Invoke-BootstrapNative -Command 'git' -Arguments @('clone', '--depth', '1', '--', $SourceRepository, $sourceClone))
    $template = Join-Path $sourceClone 'template'
    if (-not [System.IO.Directory]::Exists($template)) { throw "Source repository has no template directory: $SourceRepository" }
    if ($useExistingRepository) {
        Copy-Item -LiteralPath (Join-Path $template '.codex') -Destination $target -Recurse
        foreach ($name in @('requirements.md', 'REQUIREMENTS-PROMPT.md')) {
            $destination = Join-Path $target $name
            if ([System.IO.File]::Exists($destination)) {
                Write-Host "Preserved existing $name"
            } else {
                Copy-Item -LiteralPath (Join-Path $template $name) -Destination $destination
            }
        }
        Add-BootstrapSection `
            -Path (Join-Path $target '.gitignore') `
            -Name 'WORKTREE RALPH' `
            -Content ([System.IO.File]::ReadAllText((Join-Path $template '.gitignore'), $utf8))
        Add-BootstrapSection `
            -Path (Join-Path $target '.gitattributes') `
            -Name 'WORKTREE RALPH' `
            -Content ([System.IO.File]::ReadAllText((Join-Path $template '.gitattributes'), $utf8))
        $agentsPath = Join-Path $target 'AGENTS.md'
        if ([System.IO.File]::Exists($agentsPath)) {
            Add-BootstrapSection `
                -Path $agentsPath `
                -Name 'WORKTREE RALPH' `
                -Content ([System.IO.File]::ReadAllText((Join-Path $template 'AGENTS.md'), $utf8))
        } else {
            Copy-Item -LiteralPath (Join-Path $template 'AGENTS.md') -Destination $agentsPath
        }
    } else {
        foreach ($entry in Get-ChildItem -LiteralPath $template -Force) {
            Copy-Item -LiteralPath $entry.FullName -Destination $target -Recurse -Force
        }
    }

    $workflowPath = Join-Path $target '.codex\workflow.json'
    $workflow = [System.IO.File]::ReadAllText($workflowPath, $utf8) | ConvertFrom-Json -Depth 50
    $workflow.provider = $Provider
    $workflow.targetBranch = $targetBranch
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

    if (-not $useExistingRepository) {
        [void](Invoke-BootstrapNative -Command 'git' -Arguments @('init', '-b', $targetBranch) -WorkingDirectory $target)
    }
    if (-not [string]::IsNullOrWhiteSpace($GitUserName)) {
        [void](Invoke-BootstrapNative -Command 'git' -Arguments @('config', 'user.name', $GitUserName.Trim()) -WorkingDirectory $target)
    }
    if (-not [string]::IsNullOrWhiteSpace($GitUserEmail)) {
        [void](Invoke-BootstrapNative -Command 'git' -Arguments @('config', 'user.email', $GitUserEmail.Trim()) -WorkingDirectory $target)
    }
    $userName = (Invoke-BootstrapNative -Command 'git' -Arguments @('config', 'user.name') -WorkingDirectory $target -AllowedExitCodes @(0, 1)).Output.Trim()
    $userEmail = (Invoke-BootstrapNative -Command 'git' -Arguments @('config', 'user.email') -WorkingDirectory $target -AllowedExitCodes @(0, 1)).Output.Trim()
    if ([string]::IsNullOrWhiteSpace($userName) -or [string]::IsNullOrWhiteSpace($userEmail)) { throw 'Git user.name and user.email must be configured before bootstrap.' }

    $scriptErrors = [System.Collections.Generic.List[string]]::new()
    foreach ($script in Get-ChildItem -LiteralPath (Join-Path $target '.codex\scripts') -Filter '*.ps1') {
        $tokens = $null
        $errors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($script.FullName, [ref]$tokens, [ref]$errors)
        foreach ($parseError in @($errors)) { $scriptErrors.Add("$($script.Name): $($parseError.Message)") }
    }
    if ($scriptErrors.Count -gt 0) { throw "Copied workflow scripts are invalid: $($scriptErrors -join '; ')" }

    [void](Invoke-BootstrapNative -Command 'git' -Arguments @('add', '--force', '--', '.codex') -WorkingDirectory $target)
    [void](Invoke-BootstrapNative `
        -Command 'git' `
        -Arguments @('add', '--', '.gitignore', '.gitattributes', 'AGENTS.md', 'requirements.md', 'REQUIREMENTS-PROMPT.md') `
        -WorkingDirectory $target)
    $stagedPaths = @(
        (Invoke-BootstrapNative -Command 'git' -Arguments @('diff', '--cached', '--name-only') -WorkingDirectory $target).Lines |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    $unexpected = @(
        $stagedPaths |
            Where-Object { $_ -notmatch '^(\.codex/|\.gitignore$|\.gitattributes$|AGENTS\.md$|requirements\.md$|REQUIREMENTS-PROMPT\.md$)' }
    )
    if ($unexpected.Count -gt 0) { throw "Bootstrap staged unexpected paths: $($unexpected -join ', ')" }
    if ($stagedPaths.Count -eq 0) { throw 'Bootstrap produced no files to commit.' }
    if ('.codex/workflow.json' -notin $stagedPaths) {
        throw 'Bootstrap did not stage the required .codex workflow payload.'
    }
    Write-Host 'FILES TO COMMIT:'
    foreach ($stagedPath in $stagedPaths) { Write-Host "  $stagedPath" }
    $commitMessage = if ($useExistingRepository) { 'Install Worktree Ralph workflow' } else { 'Initialize Worktree Ralph project' }
    [void](Invoke-BootstrapNative -Command 'git' -Arguments @('commit', '-m', $commitMessage) -WorkingDirectory $target)

    if ($useExistingRepository) {
        [void](Invoke-BootstrapNative -Command 'git' -Arguments @('push', 'origin', $targetBranch) -WorkingDirectory $target)
    } elseif ($Provider -ceq 'github') {
        [void](Invoke-BootstrapNative -Command 'gh' -Arguments @('repo', 'create', $repositoryIdentity, "--$Visibility", '--source', $target, '--remote', 'origin', '--push') -WorkingDirectory $target)
    } else {
        $createdJson = (Invoke-BootstrapNative -Command 'az' -Arguments @('repos', 'create', '--organization', $AzureOrganization, '--project', $AzureProject, '--name', $ProjectName, '--output', 'json')).Output
        $created = $createdJson | ConvertFrom-Json -Depth 20
        if ([string]::IsNullOrWhiteSpace([string]$created.remoteUrl)) { throw 'Azure DevOps did not return a repository remote URL.' }
        [void](Invoke-BootstrapNative -Command 'git' -Arguments @('remote', 'add', 'origin', [string]$created.remoteUrl) -WorkingDirectory $target)
        [void](Invoke-BootstrapNative -Command 'git' -Arguments @('push', '--set-upstream', 'origin', $targetBranch) -WorkingDirectory $target)
    }

    [void](Invoke-BootstrapNative -Command 'git' -Arguments @('fetch', 'origin') -WorkingDirectory $target)
    $localSha = (Invoke-BootstrapNative -Command 'git' -Arguments @('rev-parse', 'HEAD') -WorkingDirectory $target).Output.Trim()
    $remoteSha = (Invoke-BootstrapNative -Command 'git' -Arguments @('rev-parse', "origin/$targetBranch") -WorkingDirectory $target).Output.Trim()
    if ($localSha -cne $remoteSha) { throw "Remote $targetBranch does not match the bootstrap commit." }

    . (Join-Path $target '.codex\scripts\common.ps1')
    $configuration = Get-RalphConfiguration -RepositoryRoot $target
    $paths = Initialize-RalphStateFiles -RepositoryRoot $target -Configuration $configuration
    $state = Read-RalphJson -Path $paths.State -SchemaPath (Join-Path $paths.Schemas 'state.schema.json')
    if ([string]$state.repository -cne $repositoryIdentity) { throw 'Initialized state does not match the remote repository.' }

    $bootstrapSucceeded = $true
    Write-Host ''
    Write-Host $(if ($useExistingRepository) { 'WORKTREE RALPH INSTALLED' } else { 'WORKTREE RALPH PROJECT CREATED' })
    Write-Host "PROJECT:      $target"
    Write-Host "REMOTE:       $repositoryIdentity"
    Write-Host "TARGET:       $targetBranch"
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
