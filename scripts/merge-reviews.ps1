param(
    [string]$PartsDir = $(if ($env:AI_REVIEW_PARTS_DIR) { $env:AI_REVIEW_PARTS_DIR } else { "outputs/parts" }),
    [string]$Output = $(if ($env:AI_REVIEW_OUTPUT) { $env:AI_REVIEW_OUTPUT } else { "outputs/review.md" }),
    [string]$InlineJson = $(if ($env:AI_REVIEW_INLINE_JSON) { $env:AI_REVIEW_INLINE_JSON } else { "outputs/inline-candidates.json" }),
    [string]$Only = $env:AI_REVIEW_ONLY_SLICES
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
    "merge",
    "--parts-dir", $PartsDir,
    "--output", $Output,
    "--inline-json", $InlineJson
)

if ($Only) {
    $ArgsList += @("--only", $Only)
}

& $PythonCommand $Runner @ArgsList
