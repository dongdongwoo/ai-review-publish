param(
    [string]$Diff = $(if ($env:AI_REVIEW_DIFF) { $env:AI_REVIEW_DIFF } else { "outputs/mr.diff" }),
    [string]$Slices = $(if ($env:AI_REVIEW_SLICES) { $env:AI_REVIEW_SLICES } else { "slices/example.json" }),
    [string]$PartsDir = $(if ($env:AI_REVIEW_PARTS_DIR) { $env:AI_REVIEW_PARTS_DIR } else { "outputs/parts" }),
    [string]$Output = $(if ($env:AI_REVIEW_OUTPUT) { $env:AI_REVIEW_OUTPUT } else { "outputs/review.md" }),
    [string]$InlineJson = $(if ($env:AI_REVIEW_INLINE_JSON) { $env:AI_REVIEW_INLINE_JSON } else { "outputs/inline-candidates.json" }),
    [string]$Only = $env:AI_REVIEW_ONLY_SLICES,
    [switch]$Rerun
)

$ErrorActionPreference = "Stop"

$PackageRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $PackageRoot "src/review_slices.py"

$PythonCommand = $null
foreach ($Candidate in @("py", "python3", "python")) {
    if (Get-Command $Candidate -ErrorAction SilentlyContinue) {
        $PythonCommand = $Candidate
        break
    }
}

if (-not $PythonCommand) {
    throw "Python was not found. Install Python or run src/review_slices.py with an available interpreter."
}

$ArgsList = @(
    "run",
    "--diff", $Diff,
    "--slices", $Slices,
    "--parts-dir", $PartsDir,
    "--output", $Output,
    "--inline-json", $InlineJson
)

if ($Only) {
    $ArgsList += @("--only", $Only)
}

if ($Rerun -or $env:AI_REVIEW_RERUN -eq "1") {
    $ArgsList += @("--rerun")
}

& $PythonCommand $Runner @ArgsList
