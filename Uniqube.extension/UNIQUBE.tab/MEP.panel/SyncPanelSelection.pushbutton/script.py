# -*- coding: utf-8 -*-
"""Toggle automatic panel + MEP linked selection in the view."""
from pyrevit import revit, forms, script
import panel_selection_sync as pss

uidoc = revit.uidoc


def main():
    now_on = pss.toggle(uidoc)
    if now_on:
        forms.alert(
            "Panel selection sync is ON.\n\n"
            "Click any panel stud, host MEP group, or MEP element — "
            "Revit will auto-select the full panel + MEP pair "
            "(same as Select Panel + MEP).\n\n"
            "Click this button again to turn OFF.",
            title="UNIQUBE — Sync Panel Selection",
        )
    else:
        forms.alert(
            "Panel selection sync is OFF.\n\n"
            "Use Select Panel + MEP to pick a panel manually.",
            title="UNIQUBE — Sync Panel Selection",
        )


main()
