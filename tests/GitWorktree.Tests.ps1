. (Join-Path $PSScriptRoot 'TestSupport.ps1')
$common = Join-Path (Split-Path $PSScriptRoot -Parent) 'template\.codex\scripts\common.ps1'
. $common

$temporary = New-TestDirectory
try {
    $repository = Join-Path $temporary 'repository'
    [void][System.IO.Directory]::CreateDirectory($repository)
    [void](Invoke-RalphNative git @('init','-b','main') $repository)
    [void](Invoke-RalphNative git @('config','user.name','Worktree Ralph Test') $repository)
    [void](Invoke-RalphNative git @('config','user.email','test@example.invalid') $repository)
    [System.IO.File]::WriteAllText((Join-Path $repository 'base.txt'),'base',$script:RalphUtf8)
    [void](Invoke-RalphNative git @('add','base.txt') $repository)
    [void](Invoke-RalphNative git @('commit','-m','base') $repository)
    $baseSha = (Invoke-RalphNative git @('rev-parse','HEAD') $repository).Output.Trim()
    $configuration = [pscustomobject]@{ worktreeRoot = (Join-Path $temporary 'worktrees') }
    $worktree = New-RalphWorktree -RepositoryRoot $repository -Configuration $configuration -Identity 'TASK-0001' -Branch 'worktree/TASK-0001' -BaseReference $baseSha
    [System.IO.File]::WriteAllText((Join-Path $worktree 'feature.txt'),'feature',$script:RalphUtf8)
    [void](Invoke-RalphNative git @('add','feature.txt') $worktree)
    [void](Invoke-RalphNative git @('commit','-m','feature') $worktree)
    $item = [pscustomobject]@{ allowedPaths=@('feature.txt') }
    $commit = Assert-RalphAssignmentCommit -Worktree $worktree -BaseSha $baseSha -Item $item
    Assert-TestTrue -Condition ($commit.Head -match '^[0-9a-f]{40}$') -Message 'assignment commit verified'
    Remove-RalphWorktree -RepositoryRoot $repository -Configuration $configuration -Identity 'TASK-0001' -Branch 'worktree/TASK-0001'
    Assert-TestTrue -Condition (-not [System.IO.Directory]::Exists($worktree)) -Message 'worktree removed'

    $audit = New-RalphAuditWorktree -RepositoryRoot $repository -Configuration $configuration -Reference $baseSha
    Assert-TestTrue -Condition ([System.IO.Directory]::Exists($audit)) -Message 'audit worktree created'
    Remove-RalphAuditWorktree -RepositoryRoot $repository -Configuration $configuration
    Assert-TestTrue -Condition (-not [System.IO.Directory]::Exists($audit)) -Message 'audit worktree removed'
} finally { Remove-TestDirectory $temporary }

Write-Host "Git worktree tests passed: $script:RalphTestCount assertions"
