# -*- coding: utf-8 -*-
"""Register Vibe Modeler dockable pane on pyRevit / Revit startup."""
from __future__ import print_function

import os
import sys

_lib = os.path.join(os.path.dirname(__file__), "lib")
if _lib not in sys.path:
    sys.path.append(_lib)

try:
    from pyrevit import forms, HOST_APP
    from vibe_panel import VibeModelerPanel

    if not forms.is_registered_dockable_panel(VibeModelerPanel):
        forms.register_dockable_panel(VibeModelerPanel, default_visible=False)

    # Best-effort tool bridge at startup (ribbon click still re-inits if needed)
    try:
        import vibe_tools

        if HOST_APP and getattr(HOST_APP, "uiapp", None):
            vibe_tools.ensure_external_event()
    except Exception:
        pass
except Exception:
    pass
