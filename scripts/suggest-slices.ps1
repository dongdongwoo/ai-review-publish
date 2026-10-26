param(
    [string]$Diff = $(if ($env:AI_REVIEW_DIFF) { $env:AI_REVIEW_DIFF } else { "outputs/mr.diff" }),
    [string]$Out = "slices/generated.json"
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

& $PythonCommand $Runner suggest --diff $Diff --out $Out
