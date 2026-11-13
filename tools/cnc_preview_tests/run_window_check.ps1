# Runs the Generate CNC window outside Revit, on pyRevit's own IronPython
# 2.7 engine, and saves screenshots of it.
#
#   powershell -STA -ExecutionPolicy Bypass -File run_window_check.ps1
#
# The window code has no Revit imports, so this exercises the real XAML and
# the real Python against the two production CSV files.
param(
    [string]$Driver = "window_check.py",
    [string]$OutDir = (Join-Path $PSScriptRoot "out")
)
$ErrorActionPreference = "Stop"

$engineDir = Join-Path $env:APPDATA "pyRevit-Master\bin\netfx\engines\IPY2712PR"
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$lib = Join-Path $repo "Uniqube.extension\lib"
$samples = Join-Path (Split-Path $repo -Parent) "Models added on revit\For CNC Script on Revit"

Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase, System.Xaml, System.Data
foreach ($name in "pyRevitLabs.Microsoft.Scripting", "pyRevitLabs.Microsoft.Dynamic",
                  "pyRevitLabs.IronPython") {
    [void][Reflection.Assembly]::LoadFrom((Join-Path $engineDir "$name.dll"))
}
$modules = [Reflection.Assembly]::LoadFrom((Join-Path $engineDir "pyRevitLabs.IronPython.Modules.dll"))
$loader = [Reflection.Assembly]::LoadFrom((Join-Path $engineDir "pyRevitLoader.dll"))

if (-not (Test-Path $OutDir)) { [void](New-Item -ItemType Directory -Path $OutDir) }

$engine = [IronPython.Hosting.Python]::CreateEngine()
$scope = $engine.CreateScope()
$scope.SetVariable("LIB", $lib)
$scope.SetVariable("SAMPLES", $samples)
$scope.SetVariable("OUT", $OutDir)
$scope.SetVariable("LOADER", $loader)
$scope.SetVariable("MODULES", $modules)

try {
    $engine.ExecuteFile((Join-Path $PSScriptRoot $Driver), $scope) | Out-Null
} catch {
    $inner = $_.Exception
    while ($inner.InnerException) { $inner = $inner.InnerException }
    $ops = $engine.GetType().GetMethod("GetService").MakeGenericMethod(
        [Microsoft.Scripting.Hosting.ExceptionOperations]).Invoke($engine, @(, [object[]]@()))
    Write-Host ($ops.FormatException($inner))
    exit 1
}
exit [int]$scope.GetVariable("FAILURES")
