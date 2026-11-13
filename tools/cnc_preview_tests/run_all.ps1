# Every check for the Generate CNC window, without Revit.
#
#   powershell -ExecutionPolicy Bypass -File tools\cnc_preview_tests\run_all.ps1
#
# 1. test_preview.py   CPython: the CSV read-back and its fault checks
# 2. window_check.py   the real window on pyRevit's IronPython, .NET Framework
# 3. window_check.py   the same on .NET 8, the engine Revit 2025+ uses
# 4. script_check.py   the real button script end to end, on .NET 8
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$repo = Split-Path (Split-Path $here -Parent) -Parent
$lib = Join-Path $repo "Uniqube.extension\lib"
$samples = Join-Path (Split-Path $repo -Parent) "Models added on revit\For CNC Script on Revit"
$engine = Join-Path $env:APPDATA "pyRevit-Master\bin\netcore\engines\IPY2712PR"
$hostExe = Join-Path $here "netcore_host\bin\host\netcore_host.exe"
$failed = @()

function Step($name, [scriptblock]$run) {
    Write-Host ""
    Write-Host "== $name"
    $output = & $run 2>&1
    $code = $LASTEXITCODE
    $output | Where-Object { $_ -match "FAIL|Traceback|IronPython|Error" } | ForEach-Object { Write-Host "   $_" }
    if ($code -ne 0) { $script:failed += $name; Write-Host "   exit code $code" } else { Write-Host "   passed" }
}

Step "CSV read-back (CPython)" { python (Join-Path $here "test_preview.py") }
Step "pyRevit reads the bundle: the engine is kept for the open window" {
    powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $here "bundle_check.ps1") -Quiet
}
Step "Window on .NET Framework engine" {
    powershell -STA -NoProfile -ExecutionPolicy Bypass -File (Join-Path $here "run_window_check.ps1")
}

Write-Host ""
Write-Host "== Building the .NET 8 host"
dotnet build (Join-Path $here "netcore_host") -c Release -o (Join-Path $here "netcore_host\bin\host") --nologo -v q | Out-Null
if ($LASTEXITCODE -ne 0) { throw "netcore_host did not build" }

Step "Window on .NET 8 engine (Revit 2025+)" {
    & $hostExe $engine (Join-Path $here "window_check.py") $lib $samples (Join-Path $here "out_netcore")
}
Step "Button script end to end on .NET 8" {
    & $hostExe $engine (Join-Path $here "script_check.py") $lib $samples (Join-Path $here "out_netcore")
}
Step "Run BOQ window on .NET 8" {
    & $hostExe $engine (Join-Path $here "boq_window_check.py") $lib $samples (Join-Path $here "out_netcore")
}

Write-Host ""
if ($failed.Count) { Write-Host "FAILED: $($failed -join ', ')"; exit 1 }
Write-Host "All checks passed."
