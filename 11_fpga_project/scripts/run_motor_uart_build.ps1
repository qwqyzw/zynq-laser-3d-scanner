param(
    [Parameter(Mandatory=$true)][string]$PythonPath,
    [Parameter(Mandatory=$true)][string]$VivadoPath,
    [Parameter(Mandatory=$true)][string]$BuildDirectory,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [Parameter(Mandatory=$true)][string]$RunLabel
)
# Launch this worker with Start-Process -WindowStyle Hidden. It completes outside
# an interactive tool session and writes a final status even if a stage fails.
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$reportsPath = Join-Path $projectRoot "reports"
$statusPath = Join-Path $reportsPath ($RunLabel + "_completion.json")
$runStatus = [ordered]@{
    status = "running"
    started = (Get-Date -Format o)
    build_directory = $BuildDirectory
    output_directory = $OutputDirectory
    stage = "build"
    pid = $PID
}
$runStatus | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $statusPath -Encoding utf8
Set-Location -LiteralPath $projectRoot
try {
    & $PythonPath -B -X utf8 (Join-Path $PSScriptRoot "project.py") bit --variant motor_uart --build-dir $BuildDirectory --output-dir $OutputDirectory
    if ($LASTEXITCODE -ne 0) { throw "Vivado build failed, exit=$LASTEXITCODE" }
    $runStatus.stage = "physical_audit"
    $runStatus | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $statusPath -Encoding utf8
    $auditLog = Join-Path $reportsPath ($RunLabel + "_audit.log")
    $auditJournal = Join-Path $reportsPath ($RunLabel + "_audit.jou")
    & $VivadoPath -mode batch -source (Join-Path $PSScriptRoot "audit_motor_uart_overlay.tcl") -log $auditLog -journal $auditJournal -tclargs (Join-Path $projectRoot $BuildDirectory)
    if ($LASTEXITCODE -ne 0) { throw "Physical audit failed, exit=$LASTEXITCODE" }
    $runStatus.stage = "artifact_verification"
    $runStatus | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $statusPath -Encoding utf8
    & $PythonPath -B -X utf8 (Join-Path $PSScriptRoot "verify_motor_uart_build.py") --output-dir $OutputDirectory
    if ($LASTEXITCODE -ne 0) { throw "Artifact verification failed, exit=$LASTEXITCODE" }
    $runStatus.status = "passed"
    $runStatus.stage = "complete"
} catch {
    $runStatus.status = "failed"
    $runStatus.error = $_.Exception.Message
} finally {
    $runStatus.finished = (Get-Date -Format o)
    $runStatus | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $statusPath -Encoding utf8
}
if ($runStatus.status -ne "passed") { exit 1 }
