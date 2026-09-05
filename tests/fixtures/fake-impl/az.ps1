[CmdletBinding(PositionalBinding = $false)]
param([Parameter(ValueFromRemainingArguments)][string[]]$CliArguments)

$ErrorActionPreference = 'Stop'

if ($CliArguments[0] -ceq 'account' -and $CliArguments[1] -ceq 'show') { return }
if ($CliArguments[0] -ceq 'extension' -and $CliArguments[1] -ceq 'show') { return }
throw "Unsupported fake az invocation: $($CliArguments -join ' ')"
