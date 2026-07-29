# -*- coding: utf-8 -*-
"""Toggle the Vibe Modeler dockable sidebar."""
from __future__ import print_function

__title__ = "Open Vibe"
__doc__ = "Open or hide the Vibe Modeler sidebar (registers Revit tool bridge)."
__author__ = "Uniqube"
__context__ = "zero-doc"

import os
import sys

from System import Guid
from Autodesk.Revit.UI import DockablePaneId
from pyrevit import forms, HOST_APP

PANEL_ID = "a7c3e91f-4b2d-4e8a-9f1c-6d5e8b0a2c4f"

# pushbutton -> ../../../lib  (Uniqube.extension/lib)
_lib = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib")
)
if _lib not in sys.path:
    sys.path.append(_lib)


def main():
    try:
        import vibe_tools

        vibe_tools.ensure_external_event()
    except Exception as ex:
        forms.alert(
            "Could not initialize Vibe tool bridge:\n{}\n\n"
            "Chat may work but model tools will not.".format(ex),
            title="Vibe Modeler",
        )

    try:
        pane_id = DockablePaneId(Guid(PANEL_ID))
        pane = HOST_APP.uiapp.GetDockablePane(pane_id)
        if pane.IsShown():
            pane.Hide()
        else:
            pane.Show()
    except Exception as ex:
        forms.alert(
            "Could not open Vibe Modeler.\n\n"
            "Dockable panes register at Revit startup.\n"
            "Close all documents, reload pyRevit, or restart Revit, then try again.\n\n"
            "Details:\n{}".format(ex),
            title="Vibe Modeler",
        )


if __name__ == "__main__":
    main()
