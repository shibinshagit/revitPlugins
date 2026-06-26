# -*- coding: utf-8 -*-
"""Toggle automatic panel + MEP linked selection in the view."""
from pyrevit import revit, forms, script
import panel_selection_sync as pss

uidoc = revit.uidoc
logger = script.get_logger()


def main():
    try:
        now_on = pss.toggle(uidoc)
    except Exception as ex:
        logger.debug("sync toggle failed: %s", ex)
        forms.alert(
            "Sync toggle failed:\n{}\n\n"
            "git pull revitPlugins, then pyRevit → Reload.".format(ex),
            title="UNIQUBE",
        )
        return

    if now_on:
        forms.alert(
            "Panel selection sync is ON.\n\n"
            "Click any panel stud, host group, or MEP element — "
            "Revit will auto-select the full panel + MEP pair.\n\n"
            "Click this button again to turn OFF.",
            title="UNIQUBE — Sync Panel Selection",
        )
    else:
        forms.alert(
            "Panel selection sync is OFF.\n\n"
            "Normal Revit selection is restored.",
            title="UNIQUBE — Sync Panel Selection",
        )


main()
