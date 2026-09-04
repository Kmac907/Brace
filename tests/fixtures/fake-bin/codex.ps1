[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(ValueFromPipeline)]$PipelineInput,
    [Parameter(ValueFromRemainingArguments)][string[]]$CliArguments
)

begin {
    $inputParts = [System.Collections.Generic.List[string]]::new()
}
process {
    if ($null -ne $PipelineInput) { $inputParts.Add($PipelineInput.ToString()) }
}
end {
    $schemaIndex = [Array]::IndexOf($CliArguments, '--output-schema')
    $resultIndex = [Array]::IndexOf($CliArguments, '--output-last-message')
    if ($schemaIndex -lt 0 -or $resultIndex -lt 0) { throw 'Fake Codex requires output schema and result paths.' }
    $schema = [System.IO.Path]::GetFileName($CliArguments[$schemaIndex + 1])
    $resultPath = $CliArguments[$resultIndex + 1]
    $utf8 = [System.Text.UTF8Encoding]::new($false, $true)
    $prompt = $inputParts -join [Environment]::NewLine

    switch ($schema) {
        'planning-result.schema.json' {
            $result = [ordered]@{
                status = 'complete'
                questions = @()
                normalizedRequirementsMarkdown = "# Requirements`n`n- REQ-FUNC-001: Create a feature file."
                planMarkdown = "# Plan`n`n## Feature`n`nImplement and test the required feature file."
                tasks = @(
                    [ordered]@{
                        taskId='TASK-0001';title='Create feature';description='Create the required feature file.';requirementIds=@('REQ-FUNC-001');planSections=@('Feature');dependencies=@();allowedPaths=@('feature.txt');exclusiveResources=@('feature-file');acceptanceCriteria=@('feature.txt contains implemented');checks=@('Inspect feature.txt')
                    }
                )
                summary = [ordered]@{ requirementsCount=1;taskCount=1;parallelizableTaskCount=1;assumptions=@();deferredScope=@();deferredRequirementIds=@() }
            }
        }
        'pm-blocker-result.schema.json' {
            $effects=[ordered]@{requirements='Adds the missing requirement.';plan='Adds the implementation section.';tasks='Replaces the blocked task with one follow-up task.';bugs='No bug changes.';completedWork='No integrated work changes.';schedule='Adds one task.'}
            $emptyOption=[ordered]@{requiresInput=$false;inputPrompt=$null;authorizedDocumentationPaths=@();bugDispositions=@()}
            $approve=[ordered]@{optionId='OPTION-0001';label='Approve amendment';description='Amend the contract and add a follow-up task.';recommended=$true;action='amend';requiresInput=$false;inputPrompt=$null;authorizedDocumentationPaths=@();bugDispositions=@()}
            $stop=[ordered]@{optionId='OPTION-0002';label='Stop';description='Leave the workflow blocked.';recommended=$false;action='stop';requiresInput=$false;inputPrompt=$null;authorizedDocumentationPaths=@();bugDispositions=@()}
            if($env:RALPH_FAKE_AUDIT_SCOPE_BLOCKER){$approve.description='Amend the contract and append an audit-discovered task.';$result=[ordered]@{summary='The audit found a missing implementation requirement.';recommendation='Add the requirement and append a follow-up task.';question='Approve the audit scope amendment?';amendmentRequired=$true;effects=$effects;options=@($approve,$stop);affectedTaskIds=@();affectedBugIds=@()}}
            elseif($env:RALPH_FAKE_BUG_DISPOSITION){$effects.requirements='No change.';$effects.plan='No change.';$effects.tasks='No change.';$effects.bugs='Marks BUG-0001 superseded by user decision.';$effects.schedule='Removes one obsolete bug fix.';$disposition=[ordered]@{optionId='OPTION-0001';label='Supersede bug';description='Record that the required behavior was intentionally replaced.';recommended=$true;action='disposition';requiresInput=$false;inputPrompt=$null;authorizedDocumentationPaths=@();bugDispositions=@([ordered]@{bugId='BUG-0001';disposition='superseded';evidence='The user approved the PM recommendation.'})};$result=[ordered]@{summary='The reported bug requires a product disposition.';recommendation='Mark the obsolete finding superseded.';question='Approve the bug disposition?';amendmentRequired=$false;effects=$effects;options=@($disposition,$stop);affectedTaskIds=@();affectedBugIds=@('BUG-0001')}}
            else{$result=[ordered]@{summary='The fixture requires an approved scope amendment.';recommendation='Add the missing requirement and replace the blocked task with a follow-up task.';question='Approve the recommended scope amendment?';amendmentRequired=$true;effects=$effects;options=@($approve,$stop);affectedTaskIds=@('TASK-0001');affectedBugIds=@()}}
        }
        'pm-amendment-result.schema.json' {
            $requirementsPath=Join-Path (Get-Location).Path 'requirements.md';$planPath=Join-Path (Get-Location).Path 'plan.md'
            if($prompt -notmatch 'Mode: recover_result'){
                $requirements=[IO.File]::ReadAllText($requirementsPath,$utf8);if(-not $requirements.Contains('REQ-FUNC-002')){$requirements=$requirements.TrimEnd()+"`n`n- REQ-FUNC-002: Complete the amended feature."};[IO.File]::WriteAllText($requirementsPath,$requirements,$utf8)
                $plan=[IO.File]::ReadAllText($planPath,$utf8);if(-not$plan.Contains('## Amended feature')){$plan=$plan.TrimEnd()+"`n`n## Amended feature`n`nImplement both approved requirements."};[IO.File]::WriteAllText($planPath,$plan,$utf8)
                & git add -- requirements.md plan.md;& git commit -m 'Amend project scope';if($LASTEXITCODE-ne0){throw 'Fake PM could not commit the amendment.'}
            }
            $sha=(& git rev-parse HEAD).Trim();$coordinatorRoot=@(& git worktree list --porcelain|Where-Object{$_ -like 'worktree *'}|ForEach-Object{$candidate=$_.Substring(9);if([IO.File]::Exists((Join-Path $candidate '.codex/state.json'))){$candidate}}|Select-Object -First 1);if(-not$coordinatorRoot){throw 'Fake PM could not locate coordinator state.'};$coordinatorState=[IO.File]::ReadAllText((Join-Path $coordinatorRoot '.codex/state.json'),$utf8)|ConvertFrom-Json -Depth 100;$decision=[string]$coordinatorState.activeAmendment.decisionIdentity;$option=[string]$coordinatorState.activeAmendment.selectedOptionId
            if($env:RALPH_FAKE_AUDIT_SCOPE_BLOCKER){$newTask=[ordered]@{taskId='TASK-0002';title='Implement audit scope';description='Implement the approved audit-discovered requirement.';requirementIds=@('REQ-FUNC-002');planSections=@('Amended feature');dependencies=@('TASK-0001');allowedPaths=@('expanded.txt');exclusiveResources=@('expanded-file');acceptanceCriteria=@('expanded.txt exists');checks=@('Inspect expanded.txt')};$superseded=@()}else{$newTask=[ordered]@{taskId='TASK-0002';title='Implement amended feature';description='Implement both approved requirements.';requirementIds=@('REQ-FUNC-001','REQ-FUNC-002');planSections=@('Feature','Amended feature');dependencies=@();allowedPaths=@('feature.txt');exclusiveResources=@('feature-file');acceptanceCriteria=@('feature.txt contains implemented');checks=@('Inspect feature.txt')};$superseded=@('TASK-0001')}
            $result=[ordered]@{status='completed';summary='Project contract amended.';decisionIdentity=$decision;selectedOptionId=$option;commitSha=$sha;changedFiles=@('requirements.md','plan.md');newTasks=@($newTask);supersededTaskIds=@($superseded);resumeStage='build';blocker=$null}
        }
        'builder-result.schema.json' {
            if ($env:RALPH_FAKE_OPERATIONAL_BLOCKER) {
                $result = [ordered]@{status='blocked';summary='Provider unavailable.';commitSha=$null;changedFiles=@();checks=@();blocker=[ordered]@{kind='operational';message='Provider unavailable.';evidence='Deterministic fixture outage.';affectedIdentity='TASK-0001';requiresUserDecision=$false;scopeChangePossible=$false;smallestResolution='Restore the provider and retry.';prohibitedDecisions=@('Do not amend project scope.')}}
                break
            }
            if ($env:RALPH_FAKE_RECOVER_COMMIT) {
                $sha = (& git rev-parse HEAD).Trim()
                $changed = @(& git show --pretty=format: --name-only HEAD | Where-Object { $_ })
                $result = [ordered]@{status='completed';summary='Recovered existing feature commit.';commitSha=$sha;changedFiles=$changed;checks=@([ordered]@{command='Inspect existing commit';result='passed';evidence='Existing HEAD was inspected without modification.'});blocker=$null}
                break
            }
            if($env:RALPH_FAKE_SEMANTIC_BLOCKER -and -not([IO.File]::ReadAllText((Join-Path (Get-Location).Path 'requirements.md'),$utf8).Contains('REQ-FUNC-002'))){$result=[ordered]@{status='blocked';summary='Scope decision required.';commitSha=$null;changedFiles=@();checks=@();blocker=[ordered]@{kind='scope_gap';message='A required feature is missing from the contract.';evidence='Fixture blocker evidence.';affectedIdentity='TASK-0001';requiresUserDecision=$true;scopeChangePossible=$true;smallestResolution='Add the missing requirement and replace this task.';prohibitedDecisions=@('Do not invent the missing requirement.')}};break}
            $buildFile=if($env:RALPH_FAKE_AUDIT_SCOPE_BLOCKER -and ((& git branch --show-current).Trim() -eq 'worktree/TASK-0002')){'expanded.txt'}else{'feature.txt'}
            [System.IO.File]::WriteAllText((Join-Path (Get-Location).Path $buildFile), 'implemented', $utf8)
            & git add -- $buildFile
            if ($LASTEXITCODE -ne 0) { throw 'Fake builder could not stage feature.txt.' }
            & git commit -m 'Implement feature'
            if ($LASTEXITCODE -ne 0) { throw 'Fake builder could not commit feature.txt.' }
            $sha = (& git rev-parse HEAD).Trim()
            $result = [ordered]@{ status='completed';summary='Feature implemented.';commitSha=$sha;changedFiles=@('feature.txt');checks=@([ordered]@{command='Inspect feature.txt';result='passed';evidence='File contains implemented.'});blocker=$null }
        }
        'audit-result.schema.json' {
            if($env:RALPH_FAKE_AUDIT_SCOPE_BLOCKER -and -not ([IO.File]::ReadAllText((Join-Path (Get-Location).Path 'requirements.md'),$utf8).Contains('REQ-FUNC-002'))){$result=[ordered]@{status='blocked';summary='Audit scope decision required.';bugs=@();checks=@('Inspected contract');missingEvidence=@();blocker=[ordered]@{kind='scope_gap';message='Required audit-discovered behavior is absent from the contract.';evidence='REQ-FUNC-002 is absent.';affectedIdentity=$null;requiresUserDecision=$true;scopeChangePossible=$true;smallestResolution='Approve or reject the new requirement.';prohibitedDecisions=@('Do not add scope without user approval.')}};break}
            $result = [ordered]@{
                status='completed'
                summary='One deterministic bug found.'
                bugs=@(
                    [ordered]@{bugId='BUG-0001';title='Feature lacks verification marker';severity='medium';category='correctness';requirementIds=@('REQ-FUNC-001');description='Feature requires a verification marker.';evidence='feature.txt contains only implemented.';actualBehavior='Marker absent.';requiredBehavior='Marker present.';impact='Completion cannot be verified.';requiredCorrection='Append verified.';acceptanceTest='feature.txt contains implemented and verified.';dependencies=@();allowedPaths=@('feature.txt');exclusiveResources=@('feature-file')}
                )
                checks=@('Inspected feature.txt')
                missingEvidence=@()
                blocker=$null
            }
        }
        'fixer-result.schema.json' {
            if ($env:RALPH_FAKE_BUG_DISPOSITION) {
                $result = [ordered]@{status='blocked';summary='A semantic bug disposition is required.';commitSha=$null;changedFiles=@();checks=@();blocker=[ordered]@{kind='bug_disposition';message='The required behavior may have been intentionally superseded.';evidence='The current plan admits two incompatible interpretations.';affectedIdentity='BUG-0001';requiresUserDecision=$true;scopeChangePossible=$false;smallestResolution='Ask the user whether the bug is still required.';prohibitedDecisions=@('Do not choose product behavior autonomously.')}}
                break
            }
            if ($env:RALPH_FAKE_RECOVER_COMMIT) {
                $sha = (& git rev-parse HEAD).Trim()
                $changed = @(& git show --pretty=format: --name-only HEAD | Where-Object { $_ })
                $result = [ordered]@{status='fixed';summary='Recovered existing bug-fix commit.';commitSha=$sha;changedFiles=$changed;checks=@([ordered]@{command='Inspect existing correction';result='passed';evidence='Existing HEAD was inspected without modification.'});blocker=$null}
                break
            }
            [System.IO.File]::AppendAllText((Join-Path (Get-Location).Path 'feature.txt'), "`nverified", $utf8)
            & git add -- feature.txt
            if ($LASTEXITCODE -ne 0) { throw 'Fake fixer could not stage feature.txt.' }
            & git commit -m 'Fix verification marker'
            if ($LASTEXITCODE -ne 0) { throw 'Fake fixer could not commit feature.txt.' }
            $sha = (& git rev-parse HEAD).Trim()
            $result = [ordered]@{status='fixed';summary='Verification marker added.';commitSha=$sha;changedFiles=@('feature.txt');checks=@([ordered]@{command='Inspect feature.txt';result='passed';evidence='Marker present.'});blocker=$null}
        }
        'verifier-result.schema.json' {
            $result = [ordered]@{approved=$true;summary='Focused verification passed.';findings=@();checks=@([ordered]@{command='fixture verification';result='passed';evidence='Deterministic fixture approved.'});blocker=$null}
        }
        default { throw "Unsupported fake Codex schema: $schema" }
    }

    $json = $result | ConvertTo-Json -Depth 100 -Compress
    [System.IO.File]::WriteAllText($resultPath, $json, $utf8)
    Write-Output $json
}
