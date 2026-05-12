$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$AliasName = [System.IO.Path]::GetFileNameWithoutExtension($MyInvocation.MyCommand.Name)
& (Join-Path $Here "pa.ps1") alias $AliasName @args
exit $LASTEXITCODE
