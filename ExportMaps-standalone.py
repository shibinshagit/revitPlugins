# -*- coding: utf-8 -*-
"""
Standalone: export BIMSF + colour JSON from the active Revit document.

How to run on the office PC (AnyDesk):
  1. Save this file to the Desktop as ExportMaps-standalone.py
  2. In Revit: pyRevit → Tools → Run Python Script → pick this file
     OR copy the ExportMaps.pushbutton folder into UNIQUBE.tab/Publish.panel
     and click Reload on the pyRevit tab.
  3. Pick a folder (Desktop). Send the three JSON files.

MEP: link Structure, run Fill BIMSF, then run this from the MEP file.
"""
from __future__ import print_function

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CANDIDATES = [
    os.path.join(_HERE, "Uniqube.extension", "lib"),
    os.path.join(_HERE, "lib"),
    os.path.normpath(os.path.join(_HERE, "..", "lib")),
    os.path.normpath(os.path.join(_HERE, "..", "..", "..", "lib")),
]
# Typical pyRevit user extension locations
_APPDATA = os.environ.get("APPDATA") or ""
if _APPDATA:
    _CANDIDATES.append(os.path.join(_APPDATA, "pyRevit", "Extensions", "Uniqube.extension", "lib"))
    _CANDIDATES.append(os.path.join(_APPDATA, "pyRevit-Master", "extensions", "Uniqube.extension", "lib"))

for _lib in _CANDIDATES:
    if os.path.isdir(_lib) and _lib not in sys.path:
        sys.path.append(_lib)

try:
    import uniqube_sidecar_export
except Exception as ex:
    raise Exception(
        "Could not find Uniqube lib (uniqube_sidecar_export).\n"
        "Put this script next to Uniqube.extension, or run the Export Maps "
        "button after copying ExportMaps.pushbutton into Publish.panel.\n\n%s" % ex
    )

if __name__ == "__main__":
    uniqube_sidecar_export.run()
