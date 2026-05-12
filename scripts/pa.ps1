param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$LogHelper = Join-Path $Root "shared\ps1\_log_helper.ps1"
if (Test-Path -LiteralPath $LogHelper -PathType Leaf) {
    . $LogHelper
    $flowName = Get-PAConfigString -Section "flows" -Key "devctl"
    $script:logContext = Initialize-PALogging -FlowName $flowName -ScriptType "ps1"
    $commandName = if ($Args.Count -gt 0) { $Args[0] } else { "" }
    Write-PALog -Message "devctl wrapper started command=$commandName arg_count=$($Args.Count)" -Level "INFO"
}

$DevCtl = $env:PERSONAL_ASSISTANT_DEVCTL
if (-not $DevCtl) {
    $DevCtl = Join-Path $Root "devctl.py"
}

try {
    & python $DevCtl @Args
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) {
        $exitCode = 0
    }
    if ($script:logContext) {
        $level = if ($exitCode -eq 0) { "OK" } else { "ERROR" }
        Write-PALog -Message "devctl wrapper finished exit_code=$exitCode" -Level $level
    }
    exit $exitCode
} catch {
    if ($script:logContext) {
        Write-PALog -Message "devctl wrapper failed error=$($_.Exception.Message)" -Level "ERROR"
    }
    throw
}
