param(
    [string]$Diff = "examples/input.diff",
    [string]$Output = "outputs/review.md",
    [string]$RawOutput = "outputs/review.raw.md",
    [string]$ContextOut = "outputs/context",
    [string]$Policy = "policies/generic-engineering.md",
    [string]$MrContext = $env:AI_REVIEW_MR_CONTEXT,
    [string]$AiCommand = $env:AI_REVIEW_COMMAND,
    [string]$Model = $env:AI_REVIEW_MODEL,
    [string]$SeverityThreshold = $env:AI_REVIEW_SEVERITY_THRESHOLD,
    [string]$OutputFormat = $env:AI_REVIEW_OUTPUT_FORMAT,
    [string]$Repository = "",
    [string]$MrUrl = "",
    [string]$Ticket = "",
    [string]$SourceBranch = "",
    [string]$TargetBranch = "",
    [string]$CommitSha = "",
    [string]$ChangedArea = "",
    [string]$BusinessFlow = "",
    [int]$MaxDiffLines = 2000,
    [int]$PerFileDiffLines = 300,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$PackageRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $PackageRoot "src/review_runner.py"

$PythonCommand = $null
foreach ($Candidate in @("py", "python3", "python")) {
    if (Get-Command $Candidate -ErrorAction SilentlyContinue) {
        $PythonCommand = $Candidate
        break
    }
}

if (-not $PythonCommand) {
    throw "Python was not found. Install Python or run src/review_runner.py with an available interpreter."
}

$ArgsList = @(
    "--diff", $Diff,
    "--output", $Output,
    "--raw-output", $RawOutput,
    "--context-out", $ContextOut,
    "--prompt-template", "prompts/review-prompt.md",
    "--policy", $Policy,
    "--max-diff-lines", "$MaxDiffLines",
    "--per-file-diff-lines", "$PerFileDiffLines"
)

foreach ($Pair in @(
    @("--repository", $Repository),
    @("--mr-url", $MrUrl),
    @("--ticket", $Ticket),
    @("--source-branch", $SourceBranch),
    @("--target-branch", $TargetBranch),
    @("--commit-sha", $CommitSha),
    @("--changed-area", $ChangedArea),
    @("--business-flow", $BusinessFlow),
    @("--mr-context", $MrContext)
)) {
    if ($Pair[1]) {
        $ArgsList += @($Pair[0], $Pair[1])
    }
}

if ($AiCommand) {
    $ArgsList += @("--ai-command", $AiCommand)
}

if ($Model) {
    $ArgsList += @("--model", $Model)
}

if ($SeverityThreshold) {
    $ArgsList += @("--severity-threshold", $SeverityThreshold)
}

if ($OutputFormat) {
    $ArgsList += @("--output-format", $OutputFormat)
}

if ($DryRun) {
    $ArgsList += "--dry-run"
}

if ($env:AI_REVIEW_INCLUDE_PATHS) {
    foreach ($Item in $env:AI_REVIEW_INCLUDE_PATHS.Split(",")) {
        $Trimmed = $Item.Trim()
        if ($Trimmed) {
            $ArgsList += @("--include-path", $Trimmed)
        }
    }
}

if ($env:AI_REVIEW_EXCLUDE_PATHS) {
    foreach ($Item in $env:AI_REVIEW_EXCLUDE_PATHS.Split(",")) {
        $Trimmed = $Item.Trim()
        if ($Trimmed) {
            $ArgsList += @("--exclude-path", $Trimmed)
        }
    }
}

& $PythonCommand $Runner @ArgsList
