[CmdletBinding(PositionalBinding = $false)]
param([Parameter(ValueFromPipeline)]$PipelineInput,[Parameter(ValueFromRemainingArguments)][string[]]$CliArguments)
begin { $null = $PipelineInput }
end {
    $child = Start-Process -FilePath (Get-Command pwsh).Source -ArgumentList @('-NoProfile','-NonInteractive','-Command','Start-Sleep -Seconds 300') -WindowStyle Hidden -PassThru
    [IO.File]::WriteAllText($env:RALPH_HANG_CHILD_PID, [string]$child.Id)
    Start-Sleep -Seconds 300
}
