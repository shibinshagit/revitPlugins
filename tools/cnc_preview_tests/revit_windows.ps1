# Lists Revit's visible top-level windows and whether each accepts input.
#
#   powershell -ExecutionPolicy Bypass -File tools\cnc_preview_tests\revit_windows.ps1
#
# While a dialog is open, Revit's main window reads enabled=False: Revit
# cannot be worked in. With the Generate CNC window open beside Revit it must
# read enabled=True.
Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices; using System.Collections.Generic;
public static class RevitWindows {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc p, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  public static List<string> List(uint pid) {
    var found = new List<string>();
    EnumWindows((h, l) => {
      uint p; GetWindowThreadProcessId(h, out p);
      if (p == pid && IsWindowVisible(h)) {
        var title = new StringBuilder(300); GetWindowText(h, title, 300);
        if (title.Length > 0) found.Add("enabled=" + IsWindowEnabled(h) + "  " + title);
      }
      return true; }, IntPtr.Zero);
    return found;
  }
}
"@
$revit = Get-Process Revit -ErrorAction SilentlyContinue
if (-not $revit) { "Revit is not running."; exit 0 }
foreach ($process in $revit) { [RevitWindows]::List([uint32]$process.Id) }
