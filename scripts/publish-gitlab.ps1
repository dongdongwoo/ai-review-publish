param(
    [string]$Mr = $env:AI_REVIEW_MR_URL,
    [string]$Review = $(if ($env:AI_REVIEW_OUTPUT) { $env:AI_REVIEW_OUTPUT } else { "outputs/review.md" }),
    [string]$InlineJson = $(if ($env:AI_REVIEW_INLINE_JSON) { $env:AI_REVIEW_INLINE_JSON } else { "outputs/inline-candidates.json" }),
    [string]$Out = $(if ($env:AI_REVIEW_GITLAB_PLAN) { $env:AI_REVIEW_GITLAB_PLAN } else { "outputs/gitlab-publication-plan.json" }),
    [string]$Marker = "AI REVIEW - AUTOMATED REVIEW",
    [string]$BaseSha = "",
    [string]$StartSha = "",
    [string]$HeadSha = "",
    [switch]$Apply,
    [switch]$PublishInline
)

$ErrorActionPreference = "Stop"

$PackageRoot = Split-Path -Parent $PSScriptRoot
$Publisher = Join-Path $PackageRoot "src/gitlab_publish.py"

$PythonCommand = $null
foreach ($Candidate in @("py", "python3", "python")) {
    if (Get-Command $Candidate -ErrorAction SilentlyContinue) {
        $PythonCommand = $Candidate
        break
    }
}

if (-not $PythonCommand) {
    throw "Python was not found. Install Python or run src/gitlab_publish.py with an available interpreter."
}

if (-not $Mr) {
    throw "MR URL is not configured. Pass -Mr or set AI_REVIEW_MR_URL."
}

$ArgsList = @(
    "--mr", $Mr,
    "--review", $Review,
    "--inline-json", $InlineJson,
    "--out", $Out,
    "--marker", $Marker
)

foreach ($Pair in @(
    @("--base-sha", $BaseSha),
    @("--start-sha", $StartSha),
    @("--head-sha", $HeadSha)
)) {
    if ($Pair[1]) {
        $ArgsList += @($Pair[0], $Pair[1])
    }
}

if ($Apply) {
    $ArgsList += "--apply"
}

if ($PublishInline) {
    $ArgsList += "--publish-inline"
}

& $PythonCommand $Publisher @ArgsList
