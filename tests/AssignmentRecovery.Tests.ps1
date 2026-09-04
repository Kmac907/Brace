. (Join-Path $PSScriptRoot 'TestSupport.ps1')
$sourceRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $sourceRoot 'template/.codex/scripts/common.ps1')

$temporary = New-TestDirectory
$oldPath = $env:PATH
$oldRecovery = $env:RALPH_FAKE_RECOVER_COMMIT
try {
    $repository = Join-Path $temporary 'repository'
    [void][IO.Directory]::CreateDirectory($repository)
    Copy-Item (Join-Path $sourceRoot 'template/.codex') (Join-Path $repository '.codex') -Recurse
    Copy-Item (Join-Path $sourceRoot 'template/.gitignore') (Join-Path $repository '.gitignore')
    $configurationPath = Join-Path $repository '.codex/workflow.json'
    $configuration = Read-RalphJson $configurationPath
    $configuration.github.repository = 'fixture/project'
    $configuration.worktreeRoot = Join-Path $temporary 'worktrees'
    Write-RalphTextAtomic $configurationPath ($configuration | ConvertTo-Json -Depth 30)
    Write-RalphTextAtomic (Join-Path $repository 'requirements.md') '# REQ-FUNC-001'
    Write-RalphTextAtomic (Join-Path $repository 'plan.md') '# Plan'
    [void](Invoke-RalphNative git @('init','-b','main') $repository)
    [void](Invoke-RalphNative git @('config','user.name','Worktree Ralph Test') $repository)
    [void](Invoke-RalphNative git @('config','user.email','test@example.invalid') $repository)
    [void](Invoke-RalphNative git @('add','--all') $repository)
    [void](Invoke-RalphNative git @('commit','-m','base') $repository)

    $configuration = Get-RalphConfiguration $repository
    $paths = Get-RalphPaths $repository
    foreach ($directory in @($paths.Logs,$paths.Assignments,$paths.Results)) { [void][IO.Directory]::CreateDirectory($directory) }
    $base = (Invoke-RalphNative git @('-C',$repository,'rev-parse','HEAD')).Output.Trim()
    $env:PATH = (Join-Path $sourceRoot 'tests/fixtures/fake-bin') + [IO.Path]::PathSeparator + $oldPath
    $env:RALPH_FAKE_RECOVER_COMMIT = '1'

    $task = [pscustomobject]@{taskId='TASK-0001';status='active';attemptCount=1;branch='worktree/TASK-0001';worktree=$null;baseSha=$base;resultSha=$null;allowedPaths=@('recovered-task.txt')}
    $task.worktree = New-RalphWorktree $repository $configuration $task.taskId $task.branch $base
    Write-RalphTextAtomic (Join-Path $task.worktree 'recovered-task.txt') 'committed before coordinator crash'
    [void](Invoke-RalphNative git @('-C',$task.worktree,'add','recovered-task.txt'))
    [void](Invoke-RalphNative git @('-C',$task.worktree,'commit','-m','task commit before crash'))
    $taskHead = (Invoke-RalphNative git @('-C',$task.worktree,'rev-parse','HEAD')).Output.Trim()
    $taskRecord = Recover-RalphCommittedAttempt $repository $paths $task task
    Assert-TestEqual $taskHead ([string]$taskRecord.result.commitSha) 'interrupted task commit recovered without rerun'
    Assert-TestTrue ([IO.File]::Exists((Get-RalphAttemptPath $paths result $task.taskId 1))) 'recovered task result is durable'
    Assert-TestEqual $taskHead ((Invoke-RalphNative git @('-C',$task.worktree,'rev-parse','HEAD')).Output.Trim()) 'task recovery leaves HEAD unchanged'

    [void](Invoke-RalphNative git @('-C',$repository,'worktree','remove','--force','--',$task.worktree))
    Assert-TestThrows { New-RalphWorktree $repository $configuration $task.taskId $task.branch $base ('1' * 40) } 'HEAD differs from its recorded result'
    if ([IO.Directory]::Exists($task.worktree)) { [void](Invoke-RalphNative git @('-C',$repository,'worktree','remove','--force','--',$task.worktree)) }

    $bug = [pscustomobject]@{bugId='BUG-0001';status='active';attemptCount=1;branch='worktree/BUG-0001';worktree=$null;baseSha=$base;resultSha=$null;allowedPaths=@('recovered-bug.txt')}
    $bug.worktree = New-RalphWorktree $repository $configuration $bug.bugId $bug.branch $base
    Write-RalphTextAtomic (Join-Path $bug.worktree 'recovered-bug.txt') 'fixed before coordinator crash'
    [void](Invoke-RalphNative git @('-C',$bug.worktree,'add','recovered-bug.txt'))
    [void](Invoke-RalphNative git @('-C',$bug.worktree,'commit','-m','bug fix before crash'))
    $bugHead = (Invoke-RalphNative git @('-C',$bug.worktree,'rev-parse','HEAD')).Output.Trim()
    $bugRecord = Recover-RalphCommittedAttempt $repository $paths $bug bug
    Assert-TestEqual $bugHead ([string]$bugRecord.result.commitSha) 'interrupted bug commit recovered without rerun'
    Assert-TestTrue ([IO.File]::Exists((Get-RalphAttemptPath $paths result $bug.bugId 1))) 'recovered bug result is durable'

    Write-RalphTextAtomic (Join-Path $bug.worktree 'second.txt') 'second commit'
    [void](Invoke-RalphNative git @('-C',$bug.worktree,'add','second.txt'))
    [void](Invoke-RalphNative git @('-C',$bug.worktree,'commit','-m','unexpected second commit'))
    Assert-TestThrows { Assert-RalphAssignmentCommit $bug.worktree $base $bug } 'exactly one commit'
}
finally {
    $env:PATH = $oldPath
    $env:RALPH_FAKE_RECOVER_COMMIT = $oldRecovery
    Remove-TestDirectory $temporary
}

Write-Host "Assignment recovery tests passed: $script:RalphTestCount assertions"
