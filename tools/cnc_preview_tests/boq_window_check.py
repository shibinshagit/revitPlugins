# -*- coding: utf-8 -*-
"""Drive the Run BOQ window outside Revit (IronPython / netcore host)."""
import sys

import clr

clr.AddReference(MODULES)
from IronPython.Modules import ResourceMetaPathImporter

sys.meta_path.append(
    ResourceMetaPathImporter(LOADER, "pyRevitLoader.python_2712pr_lib.zip")
)
sys.path.insert(0, LIB)

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System import Action, Environment
from System.Windows.Threading import DispatcherPriority

import uniqube_brand as brand
from uniqube_cnc_window import ImmediateRunner
from unibom.ui import dialogs

FAILURES = 0


def check(label, condition, detail=""):
    global FAILURES
    if condition:
        print("  ok    " + label)
    else:
        FAILURES += 1
        print("  FAIL  " + label + ("  -> " + str(detail) if detail else ""))


print("IronPython {} on .NET {}".format(sys.version.split(" (")[0], Environment.Version))
print("brand")
check("header logo file exists", brand.logo_path(dark=True, header=True) is not None
      and __import__("os").path.isfile(brand.logo_path(dark=True, header=True)),
      brand.logo_path(dark=True, header=True))

CALLS = []
REPORTS = []


def takeoff(options):
    CALLS.append(options.method)
    return {
        "ok": True,
        "title": "Takeoff finished",
        "summary": "Counted: 10 elements, 2 walls\n1 file written to {}".format(
            options.folder),
        "folder": options.folder,
        "files": ["demo.csv"],
    }


window = dialogs.RunBoqDialog(
    "TEST MODEL",
    "Level 1",
    3,
    OUT,
    runner=ImmediateRunner(),
    on_run=takeoff,
    pick_folder=lambda: OUT,
    stay_open=True,
)
win = window.window


def settle():
    win.Dispatcher.Invoke(DispatcherPriority.ApplicationIdle, Action(lambda: None))


window.show()
settle()
print("window")
check("logo loaded", window.brand_logo.Source is not None)
check("footer reads Close", window.cancel_button.Content == "Close")
check("selection is offered", window.rb_selection.IsEnabled and bool(window.rb_selection.IsChecked))
check("title shows the model", window.model_text.Text == "TEST MODEL")

window._folder_click(None, None)
settle()
check("folder can change", window.folder_text.Text == OUT, window.folder_text.Text)

window.rb_counted.IsChecked = True
window._run_click(None, None)
settle()
check("takeoff ran", CALLS == ["counted"], CALLS)
check("report overlay shown", str(window.report_overlay.Visibility) == "Visible")
check("report title", window.report_title.Text == "Takeoff finished")
check("no handler raised", window.errors == [], window.errors)

window._hide_report()
win.Close()
settle()
check("closes cleanly", not window.is_open)

print("")
print("FAILURES: {}".format(FAILURES))
