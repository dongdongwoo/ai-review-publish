param()

$ErrorActionPreference = "Stop"

$PackageRoot = Split-Path -Parent $PSScriptRoot

$PythonCommand = $null
foreach ($Candidate in @("py", "python3", "python")) {
    if (Get-Command $Candidate -ErrorAction SilentlyContinue) {
        $PythonCommand = $Candidate
        break
    }
}

if (-not $PythonCommand) {
    throw "Python was not found."
}

& $PythonCommand -m unittest discover -s $PSScriptRoot -p "test_*.py"
