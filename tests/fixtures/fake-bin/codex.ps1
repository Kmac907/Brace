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
        'builder-result.schema.json' {
            [System.IO.File]::WriteAllText((Join-Path (Get-Location).Path 'feature.txt'), 'implemented', $utf8)
            & git add -- feature.txt
            if ($LASTEXITCODE -ne 0) { throw 'Fake builder could not stage feature.txt.' }
            & git commit -m 'Implement feature'
            if ($LASTEXITCODE -ne 0) { throw 'Fake builder could not commit feature.txt.' }
            $sha = (& git rev-parse HEAD).Trim()
            $result = [ordered]@{ status='completed';summary='Feature implemented.';commitSha=$sha;changedFiles=@('feature.txt');checks=@([ordered]@{command='Inspect feature.txt';result='passed';evidence='File contains implemented.'});blocker=$null }
        }
        'audit-result.schema.json' {
            $result = [ordered]@{
                summary='One deterministic bug found.'
                bugs=@(
                    [ordered]@{bugId='BUG-0001';title='Feature lacks verification marker';severity='medium';category='correctness';requirementIds=@('REQ-FUNC-001');description='Feature requires a verification marker.';evidence='feature.txt contains only implemented.';actualBehavior='Marker absent.';requiredBehavior='Marker present.';impact='Completion cannot be verified.';requiredCorrection='Append verified.';acceptanceTest='feature.txt contains implemented and verified.';dependencies=@();allowedPaths=@('feature.txt');exclusiveResources=@('feature-file')}
                )
                checks=@('Inspected feature.txt')
                missingEvidence=@()
            }
        }
        'fixer-result.schema.json' {
            [System.IO.File]::AppendAllText((Join-Path (Get-Location).Path 'feature.txt'), "`nverified", $utf8)
            & git add -- feature.txt
            if ($LASTEXITCODE -ne 0) { throw 'Fake fixer could not stage feature.txt.' }
            & git commit -m 'Fix verification marker'
            if ($LASTEXITCODE -ne 0) { throw 'Fake fixer could not commit feature.txt.' }
            $sha = (& git rev-parse HEAD).Trim()
            $result = [ordered]@{status='fixed';summary='Verification marker added.';commitSha=$sha;changedFiles=@('feature.txt');checks=@([ordered]@{command='Inspect feature.txt';result='passed';evidence='Marker present.'});blocker=$null}
        }
        'verifier-result.schema.json' {
            $result = [ordered]@{approved=$true;summary='Focused verification passed.';findings=@();checks=@([ordered]@{command='fixture verification';result='passed';evidence='Deterministic fixture approved.'})}
        }
        default { throw "Unsupported fake Codex schema: $schema" }
    }

    $json = $result | ConvertTo-Json -Depth 100 -Compress
    [System.IO.File]::WriteAllText($resultPath, $json, $utf8)
    Write-Output $json
}
