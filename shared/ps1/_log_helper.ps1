# Purpose : Shared PowerShell logger for personal-assistant.
# Schema  : logs/unified/<flow>.log + logs/unified/_session.log
# Format  : [ts] [PS1] [flow] [LEVEL] message
# Mirrors : ultracode-launcher/shared/ps1/_log_helper.ps1 and jar/helper/logging-helper.ps1.

function Resolve-PersonalAssistantRoot {
    function script:_IsPersonalAssistantRoot([string]$Path) {
        return $Path `
            -and (Test-Path -LiteralPath (Join-Path $Path "README.md") -PathType Leaf) `
            -and (Test-Path -LiteralPath (Join-Path $Path "PLAN.md") -PathType Leaf) `
            -and (Test-Path -LiteralPath (Join-Path $Path "shared\ps1\_log_helper.ps1") -PathType Leaf)
    }

    $candidate = $PSScriptRoot
    for ($i = 0; $i -lt 8 -and $candidate; $i++) {
        if (_IsPersonalAssistantRoot $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
        $parent = Split-Path -Parent $candidate
        if (-not $parent -or $parent -eq $candidate) { break }
        $candidate = $parent
    }

    throw "Could not resolve personal-assistant root from: $PSScriptRoot"
}

function Test-PADebugLogsEnabled {
    if ($script:_paVerboseLog) { return $true }
    $envVar = Get-PAConfigString -Section "logging" -Key "debug_env_var"
    $enabledValues = Get-PAConfigStringList -Section "logging" -Key "debug_enabled_values"
    $value = [Environment]::GetEnvironmentVariable($envVar)
    return ($value -and $enabledValues -contains $value.Trim().ToLowerInvariant())
}

function Get-PAConfigLines {
    $repoRoot = Resolve-PersonalAssistantRoot
    $configPath = Join-Path $repoRoot "config\settings.toml"
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        throw "Missing personal-assistant config: $configPath"
    }
    return Get-Content -LiteralPath $configPath
}

function Get-PAConfigRawValue {
    param(
        [Parameter(Mandatory = $true)][string]$Section,
        [Parameter(Mandatory = $true)][string]$Key
    )

    $inSection = $false
    foreach ($line in Get-PAConfigLines) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        if ($trimmed -match '^\[(.+)\]$') {
            $inSection = ($Matches[1] -eq $Section)
            continue
        }
        if ($inSection -and $trimmed -match "^$([regex]::Escape($Key))\s*=\s*(.+)$") {
            return $Matches[1].Trim()
        }
    }

    throw "Missing config key: [$Section].$Key"
}

function Get-PAConfigInt {
    param(
        [Parameter(Mandatory = $true)][string]$Section,
        [Parameter(Mandatory = $true)][string]$Key
    )
    return [int](Get-PAConfigRawValue -Section $Section -Key $Key)
}

function Get-PAConfigString {
    param(
        [Parameter(Mandatory = $true)][string]$Section,
        [Parameter(Mandatory = $true)][string]$Key
    )
    $raw = Get-PAConfigRawValue -Section $Section -Key $Key
    return $raw.Trim('"').Trim("'")
}

function Get-PAConfigStringList {
    param(
        [Parameter(Mandatory = $true)][string]$Section,
        [Parameter(Mandatory = $true)][string]$Key
    )
    $raw = Get-PAConfigRawValue -Section $Section -Key $Key
    $inside = $raw.Trim()
    if (-not ($inside.StartsWith("[") -and $inside.EndsWith("]"))) {
        throw "Config key [$Section].$Key is not a string list"
    }
    $inside = $inside.Substring(1, $inside.Length - 2)
    if (-not $inside.Trim()) { return @() }
    return @(
        $inside -split "," |
            ForEach-Object { $_.Trim().Trim('"').Trim("'").ToLowerInvariant() } |
            Where-Object { $_ }
    )
}

function Invoke-PALogRoll {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][long]$MaxBytes)
    try {
        if ((Test-Path -LiteralPath $Path) -and (Get-Item -LiteralPath $Path).Length -gt $MaxBytes) {
            $dir = Split-Path -Parent $Path
            $base = [System.IO.Path]::GetFileNameWithoutExtension($Path)
            $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
            Move-Item -LiteralPath $Path -Destination (Join-Path $dir "$base`_$stamp.log") -Force
        }
    } catch {}
}

function Invoke-PALogCleanup {
    param([Parameter(Mandatory = $true)][string]$Dir, [Parameter(Mandatory = $true)][int]$RetentionDays)
    if (-not (Test-Path -LiteralPath $Dir)) { return }
    $cutoff = (Get-Date).AddDays(-$RetentionDays)
    try {
        Get-ChildItem -LiteralPath $Dir -Filter "*.log" -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTime -lt $cutoff } |
            ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue }
    } catch {}
}

function Initialize-PALogging {
    param(
        [Parameter(Mandatory = $true)][string]$FlowName,
        [string]$ScriptType = "ps1",
        [Nullable[int]]$RollSizeMB,
        [Nullable[int]]$RetentionDays,
        [switch]$VerboseLog
    )

    $script:_paVerboseLog = $VerboseLog.IsPresent
    $repoRoot = Resolve-PersonalAssistantRoot
    $resolvedRollSizeMB = if ($RollSizeMB.HasValue) { $RollSizeMB.Value } else { Get-PAConfigInt -Section "logging" -Key "roll_size_mb" }
    $resolvedRetentionDays = if ($RetentionDays.HasValue) { $RetentionDays.Value } else { Get-PAConfigInt -Section "logging" -Key "retention_days" }
    $langDir = Join-Path $repoRoot "logs\$ScriptType"
    $unifiedDir = Join-Path $repoRoot "logs\unified"

    if (-not (Test-Path -LiteralPath $langDir)) { New-Item -ItemType Directory -Path $langDir -Force | Out-Null }
    if (-not (Test-Path -LiteralPath $unifiedDir)) { New-Item -ItemType Directory -Path $unifiedDir -Force | Out-Null }

    $context = [pscustomobject]@{
        FlowName = $FlowName
        ScriptType = $ScriptType
        LangDir = $langDir
        UnifiedDir = $unifiedDir
        LangLogFile = Join-Path $langDir "$FlowName.log"
        UnifiedLogFile = Join-Path $unifiedDir "$FlowName.log"
        SessionLogFile = Join-Path $unifiedDir "_session.log"
        RollBytes = $resolvedRollSizeMB * 1MB
        RetentionDays = $resolvedRetentionDays
    }

    foreach ($path in @($context.LangLogFile, $context.UnifiedLogFile, $context.SessionLogFile)) {
        Invoke-PALogRoll -Path $path -MaxBytes $context.RollBytes
    }
    Invoke-PALogCleanup -Dir (Join-Path $repoRoot "logs") -RetentionDays $RetentionDays

    return $context
}

function Write-PALog {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [ValidateSet("TRACE", "DEBUG", "INFO", "WARN", "ERROR", "OK")][string]$Level = "INFO"
    )

    if (-not $script:logContext) {
        Write-Host "[$Level] $Message"
        return
    }

    if (($Level -eq "DEBUG" -or $Level -eq "TRACE") -and -not (Test-PADebugLogsEnabled)) {
        return
    }

    $timestamp = Get-Date -Format "yyyy-MM-dd HH\:mm\:ss"
    $unifiedLine = "[{0}] [PS1] [{1}] [{2}] {3}" -f $timestamp, $script:logContext.FlowName, $Level, $Message
    $langLine = "[{0}] [{1}] {2}" -f $timestamp, $Level, $Message
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)

    try { [System.IO.File]::AppendAllText($script:logContext.UnifiedLogFile, $unifiedLine + [Environment]::NewLine, $utf8NoBom) } catch {}
    try { [System.IO.File]::AppendAllText($script:logContext.SessionLogFile, $unifiedLine + [Environment]::NewLine, $utf8NoBom) } catch {}
    try { [System.IO.File]::AppendAllText($script:logContext.LangLogFile, $langLine + [Environment]::NewLine, $utf8NoBom) } catch {}

    $consoleEnabled = $false
    try {
        $consoleEnabled = [System.Convert]::ToBoolean((Get-PAConfigString -Section "logging" -Key "ps1_console_enabled"))
    } catch {}
    if (-not $consoleEnabled) {
        return
    }

    switch ($Level) {
        "WARN" { Write-Host "[WARN] $Message" -ForegroundColor Yellow; break }
        "ERROR" { Write-Host "[ERROR] $Message" -ForegroundColor Red; break }
        "OK" { Write-Host "[OK] $Message" -ForegroundColor Green; break }
        default { Write-Host "[INFO] $Message" -ForegroundColor Green; break }
    }
}

function Write-FlowLog {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [ValidateSet("TRACE", "DEBUG", "INFO", "WARN", "ERROR", "OK")][string]$Level = "INFO"
    )
    Write-PALog -Message $Message -Level $Level
}
