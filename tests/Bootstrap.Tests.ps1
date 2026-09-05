. (Join-Path $PSScriptRoot 'TestSupport.ps1')

function Initialize-ExistingFixtureRepository {
    param(
        [Parameter(Mandatory)][string]$RepositoryPath,
        [Parameter(Mandatory)][string]$RemotePath,
        [Parameter(Mandatory)][string]$HostedUrl
    )

    [void](& git init --bare $RemotePath 2>&1)
    [void][System.IO.Directory]::CreateDirectory($RepositoryPath)
    [void](& git -C $RepositoryPath init -b main 2>&1)
    [void](& git -C $RepositoryPath config user.name 'Worktree Ralph Test' 2>&1)
    [void](& git -C $RepositoryPath config user.email 'test@example.invalid' 2>&1)
    [System.IO.File]::WriteAllText((Join-Path $RepositoryPath 'source.txt'), "original source`n")
    [void](& git -C $RepositoryPath add source.txt 2>&1)
    [void](& git -C $RepositoryPath commit -m initial 2>&1)
    $normalizedRemote = $RemotePath.Replace('\', '/')
    [void](& git -C $RepositoryPath config "url.$normalizedRemote.insteadOf" $HostedUrl 2>&1)
    [void](& git -C $RepositoryPath remote add origin $HostedUrl 2>&1)
    [void](& git -C $RepositoryPath push --set-upstream origin main 2>&1)
    [void](& git -C $RepositoryPath remote set-head origin main 2>&1)
}

$temporary = New-TestDirectory
$originalPath = $env:PATH
$originalState = $env:RALPH_FAKE_GH_STATE
$originalRemote = $env:RALPH_FAKE_BOOTSTRAP_REMOTE
try {
    $repositoryRoot = Split-Path $PSScriptRoot -Parent
    $bootstrap = Join-Path $repositoryRoot 'New-WorktreeRalphProject.ps1'
    $source = Join-Path $temporary 'source'
    $projects = Join-Path $temporary 'projects'
    [void][System.IO.Directory]::CreateDirectory($source)
    [void][System.IO.Directory]::CreateDirectory($projects)
    Copy-Item -LiteralPath (Join-Path $repositoryRoot 'template') -Destination $source -Recurse
    [void](& git -C $source init -b main 2>&1)
    [void](& git -C $source config user.name 'Worktree Ralph Test' 2>&1)
    [void](& git -C $source config user.email 'test@example.invalid' 2>&1)
    [void](& git -C $source add --all 2>&1)
    [void](& git -C $source commit -m template 2>&1)

    $env:PATH = (Join-Path $PSScriptRoot 'fixtures\fake-bin') + [IO.Path]::PathSeparator + $originalPath
    $env:RALPH_FAKE_GH_STATE = Join-Path $temporary 'pull-requests.json'

    # The original new-project path remains available for unattended bootstrap.
    $newRemote = Join-Path $temporary 'new-remote.git'
    $env:RALPH_FAKE_BOOTSTRAP_REMOTE = $newRemote
    & $bootstrap `
        -SourceRepository $source `
        -ProjectName 'fixture-project' `
        -ParentDirectory $projects `
        -Provider github `
        -GitHubOwner fixture-owner `
        -GitUserName 'Worktree Ralph Test' `
        -GitUserEmail 'test@example.invalid'

    $newTarget = Join-Path $projects 'fixture-project'
    Assert-TestTrue -Condition ([IO.File]::Exists((Join-Path $newTarget 'requirements.md'))) -Message 'new project requirements template copied'
    Assert-TestTrue -Condition ([IO.File]::Exists((Join-Path $newTarget 'REQUIREMENTS-PROMPT.md'))) -Message 'new project requirements prompt copied'
    Assert-TestTrue -Condition ([IO.File]::Exists((Join-Path $newTarget '.codex\scripts\planning-loop.ps1'))) -Message 'new project planning loop copied'
    Assert-TestTrue -Condition ([IO.File]::Exists((Join-Path $newTarget '.codex\state.json'))) -Message 'new project workflow state initialized'
    $newWorkflow = [IO.File]::ReadAllText((Join-Path $newTarget '.codex\workflow.json')) | ConvertFrom-Json
    Assert-TestEqual -Expected 'fixture-owner/fixture-project' -Actual ([string]$newWorkflow.github.repository) -Message 'new project provider identity configured'
    $newLocal = (& git -C $newTarget rev-parse HEAD).Trim()
    $newPublished = (& git -C $newTarget rev-parse origin/main).Trim()
    Assert-TestEqual -Expected $newLocal -Actual $newPublished -Message 'new project initial commit published'
    Assert-TestTrue -Condition (-not [IO.Directory]::Exists((Join-Path $newTarget 'template'))) -Message 'new project receives only template contents'

    # An existing GitHub repository keeps its source and documentation while receiving the workflow.
    $existingGitHub = Join-Path $projects 'existing-github'
    $existingGitHubRemote = Join-Path $temporary 'existing-github.git'
    $githubUrl = 'https://github.com/fixture-owner/existing-github.git'
    Initialize-ExistingFixtureRepository -RepositoryPath $existingGitHub -RemotePath $existingGitHubRemote -HostedUrl $githubUrl
    [System.IO.File]::WriteAllText((Join-Path $existingGitHub 'requirements.md'), "existing requirements`n")
    [System.IO.File]::WriteAllText((Join-Path $existingGitHub 'REQUIREMENTS-PROMPT.md'), "existing prompt`n")
    [System.IO.File]::WriteAllText((Join-Path $existingGitHub 'AGENTS.md'), "# Existing agent rules`n")
    [System.IO.File]::WriteAllText((Join-Path $existingGitHub '.gitignore'), "custom-output/`n.codex/`n")
    [System.IO.File]::WriteAllText((Join-Path $existingGitHub '.gitattributes'), "*.custom text`n")
    [void](& git -C $existingGitHub add --all 2>&1)
    [void](& git -C $existingGitHub commit -m 'add existing project files' 2>&1)
    [void](& git -C $existingGitHub push origin main 2>&1)
    $sourceBefore = [IO.File]::ReadAllText((Join-Path $existingGitHub 'source.txt'))

    & $bootstrap -SourceRepository $source -ExistingRepositoryPath $existingGitHub

    Assert-TestEqual -Expected $sourceBefore -Actual ([IO.File]::ReadAllText((Join-Path $existingGitHub 'source.txt'))) -Message 'existing source preserved'
    Assert-TestEqual -Expected "existing requirements`n" -Actual ([IO.File]::ReadAllText((Join-Path $existingGitHub 'requirements.md'))) -Message 'existing requirements preserved'
    Assert-TestEqual -Expected "existing prompt`n" -Actual ([IO.File]::ReadAllText((Join-Path $existingGitHub 'REQUIREMENTS-PROMPT.md'))) -Message 'existing prompt preserved'
    Assert-TestTrue -Condition ([IO.File]::ReadAllText((Join-Path $existingGitHub 'AGENTS.md')).Contains('# BEGIN WORKTREE RALPH')) -Message 'agent instructions merged'
    Assert-TestTrue -Condition ([IO.File]::ReadAllText((Join-Path $existingGitHub '.gitignore')).Contains('custom-output/')) -Message 'existing ignore rules preserved'
    Assert-TestTrue -Condition ([IO.File]::ReadAllText((Join-Path $existingGitHub '.gitignore')).Contains('# BEGIN WORKTREE RALPH')) -Message 'workflow ignore rules merged'
    Assert-TestTrue -Condition ([IO.File]::ReadAllText((Join-Path $existingGitHub '.gitattributes')).Contains('*.custom text')) -Message 'existing attributes preserved'
    Assert-TestTrue -Condition ([IO.File]::Exists((Join-Path $existingGitHub '.codex\state.json'))) -Message 'existing repository workflow state initialized'
    Assert-TestTrue -Condition (@(& git -C $existingGitHub ls-files --error-unmatch .codex/workflow.json 2>$null).Count -gt 0) -Message 'workflow is tracked even when existing rules ignored .codex'
    $githubWorkflow = [IO.File]::ReadAllText((Join-Path $existingGitHub '.codex\workflow.json')) | ConvertFrom-Json
    Assert-TestEqual -Expected 'fixture-owner/existing-github' -Actual ([string]$githubWorkflow.github.repository) -Message 'existing GitHub identity detected'
    Assert-TestEqual -Expected 'main' -Actual ([string]$githubWorkflow.targetBranch) -Message 'existing target branch detected'
    Assert-TestEqual -Expected ((& git -C $existingGitHub rev-parse HEAD).Trim()) -Actual ((& git -C $existingGitHub rev-parse origin/main).Trim()) -Message 'existing installation commit published'
    Assert-TestEqual -Expected '' -Actual ((& git -C $existingGitHub status --porcelain --untracked-files=all) -join '') -Message 'existing repository remains clean'
    Assert-TestThrows -Action { & $bootstrap -SourceRepository $source -ExistingRepositoryPath $existingGitHub } -Pattern 'already contains \.codex'

    # Azure DevOps identity is detected from the existing origin rather than re-entered.
    $existingAzure = Join-Path $projects 'existing-azure'
    $existingAzureRemote = Join-Path $temporary 'existing-azure.git'
    $azureUrl = 'https://dev.azure.com/fixture-org/Fixture%20Project/_git/existing-azure'
    Initialize-ExistingFixtureRepository -RepositoryPath $existingAzure -RemotePath $existingAzureRemote -HostedUrl $azureUrl
    & $bootstrap -SourceRepository $source -ExistingRepositoryPath $existingAzure
    $azureWorkflow = [IO.File]::ReadAllText((Join-Path $existingAzure '.codex\workflow.json')) | ConvertFrom-Json
    Assert-TestEqual -Expected 'azure_devops' -Actual ([string]$azureWorkflow.provider) -Message 'Azure provider detected'
    Assert-TestEqual -Expected 'https://dev.azure.com/fixture-org' -Actual ([string]$azureWorkflow.azureDevOps.organization) -Message 'Azure organization detected'
    Assert-TestEqual -Expected 'Fixture Project' -Actual ([string]$azureWorkflow.azureDevOps.project) -Message 'Azure project decoded'
    Assert-TestEqual -Expected 'existing-azure' -Actual ([string]$azureWorkflow.azureDevOps.repository) -Message 'Azure repository detected'
    Assert-TestEqual -Expected ((& git -C $existingAzure rev-parse HEAD).Trim()) -Actual ((& git -C $existingAzure rev-parse origin/main).Trim()) -Message 'Azure installation commit published'

    # Unsafe adoption states are rejected before installing files.
    $dirty = Join-Path $projects 'dirty-existing'
    $dirtyRemote = Join-Path $temporary 'dirty-existing.git'
    Initialize-ExistingFixtureRepository -RepositoryPath $dirty -RemotePath $dirtyRemote -HostedUrl 'https://github.com/fixture-owner/dirty-existing.git'
    [System.IO.File]::WriteAllText((Join-Path $dirty 'untracked.txt'), 'do not overwrite')
    Assert-TestThrows -Action { & $bootstrap -SourceRepository $source -ExistingRepositoryPath $dirty } -Pattern 'worktree is not clean'
    Assert-TestTrue -Condition (-not [IO.Directory]::Exists((Join-Path $dirty '.codex'))) -Message 'dirty repository was not modified'

    $notRepository = Join-Path $projects 'not-a-repository'
    [void][IO.Directory]::CreateDirectory($notRepository)
    Assert-TestThrows -Action { & $bootstrap -SourceRepository $source -ExistingRepositoryPath $notRepository } -Pattern 'not a Git repository'
    Assert-TestThrows -Action { & $bootstrap -SourceRepository $source -ExistingRepositoryPath $existingGitHub -ProjectName conflict } -Pattern 'cannot be combined'
} finally {
    $env:PATH = $originalPath
    $env:RALPH_FAKE_GH_STATE = $originalState
    $env:RALPH_FAKE_BOOTSTRAP_REMOTE = $originalRemote
    Remove-TestDirectory $temporary
}

Write-Host "Bootstrap tests passed: $script:RalphTestCount assertions"
