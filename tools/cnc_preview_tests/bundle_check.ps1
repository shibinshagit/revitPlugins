# Asks pyRevit's own bundle parser what it reads from the Generate CNC bundle.
#
#   powershell -ExecutionPolicy Bypass -File tools\cnc_preview_tests\bundle_check.ps1
#
# The window can only stay open beside Revit if pyRevit gives the button a
# persistent engine, and pyRevit decides that from bundle.yaml when it loads.
# This runs the parser pyRevit's loader uses (pyRevitExtensionParser.dll) on
# the real file, so a bundle the loader does not understand fails here rather
# than silently in Revit. -Path checks another bundle.yaml.
param(
    [string]$Path = "",
    [switch]$Quiet
)
$ErrorActionPreference = "Stop"
$bin = Join-Path $env:APPDATA "pyRevit-Master\bin\netfx"
$engine = Join-Path $bin "engines\IPY2712PR"
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not $Path) {
    $Path = Join-Path $repo "Uniqube.extension\UNIQUBE.tab\Structural.panel\GenerateCNC.pushbutton\bundle.yaml"
}

[void][Reflection.Assembly]::LoadFrom((Join-Path $bin "YamlDotNet.dll"))
$parserAsm = [Reflection.Assembly]::LoadFrom((Join-Path $engine "pyRevitExtensionParser.dll"))
$parse = $parserAsm.GetType("pyRevitExtensionParser.BundleParser+BundleYamlParser").GetMethod("Parse")

try {
    $bundle = $parse.Invoke($null, @($Path))
} catch {
    $inner = $_.Exception
    while ($inner.InnerException) { $inner = $inner.InnerException }
    Write-Host "  FAIL  pyRevit could not read $Path"
    Write-Host "        -> $($inner.GetType().Name): $($inner.Message)"
    Write-Host "FAILURES: 1"
    exit 1
}

$persistent = [bool]$bundle.Engine.Persistent
if (-not $Quiet) {
    Write-Host "  file  $Path"
    $bundle.GetType().GetProperties() | ForEach-Object {
        $value = $_.GetValue($bundle, $null)
        if ($value -is [string] -and $value) { Write-Host ("        {0} = {1}" -f $_.Name, $value) }
    }
    $bundle.Engine.GetType().GetProperties() | ForEach-Object {
        Write-Host ("        Engine.{0} = {1}" -f $_.Name, $_.GetValue($bundle.Engine, $null))
    }
}
if ($persistent) {
    Write-Host "  ok    pyRevit reads a persistent engine from the bundle"
    Write-Host "FAILURES: 0"
    exit 0
}
Write-Host "  FAIL  pyRevit reads a persistent engine from the bundle  -> Engine.Persistent is False"
Write-Host "FAILURES: 1"
exit 1
