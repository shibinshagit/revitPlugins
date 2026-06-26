# -*- coding: utf-8 -*-
"""Toggle automatic panel + MEP linked selection in the view."""
from pyrevit import revit, forms, script
import panel_selection_sync as pss

uidoc = revit.uidoc
logger = script.get_logger()


def main():
    try:
        currently_on = pss.is_enabled(uidoc)
    except Exception as ex:
        logger.debug("sync state check failed: %s", ex)
        forms.alert(
            "Could not read sync state:\n{}\n\n"
            "git pull revitPlugins, then pyRevit → Reload.".format(ex),
            title="UNIQUBE",
        )
        return

    if currently_on:
        turn_off = forms.alert(
            "Panel selection sync is currently ON.\n\n"
            "Clicking panel or MEP auto-selects the full panel pair.\n\n"
            "Turn sync OFF?",
            yes=True,
            no=True,
            title="UNIQUBE — Sync Panel Selection",
        )
        if not turn_off:
            return
        try:
            pss.disable(uidoc)
        except Exception as ex:
            logger.debug("sync disable failed: %s", ex)
            forms.alert("Could not turn sync OFF:\n{}".format(ex), title="UNIQUBE")
            return
        forms.alert(
            "Panel selection sync is now OFF.\n\n"
            "Normal Revit selection is restored.\n\n"
            "If clicks still auto-select the whole panel, use pyRevit → Reload once "
            "(clears old handlers from a previous version).",
            title="UNIQUBE — Sync Panel Selection",
        )
        return

    turn_on = forms.alert(
        "Panel selection sync is currently OFF.\n\n"
        "Turn sync ON?\n\n"
        "When ON, clicking any panel stud, host group, or MEP element "
        "auto-selects the full panel + MEP pair.",
        yes=True,
        no=True,
        title="UNIQUBE — Sync Panel Selection",
    )
    if not turn_on:
        return
    try:
        pss.enable(uidoc)
    except Exception as ex:
        logger.debug("sync enable failed: %s", ex)
        forms.alert("Could not turn sync ON:\n{}".format(ex), title="UNIQUBE")
        return
    forms.alert(
        "Panel selection sync is now ON.\n\n"
        "Click this button again to turn OFF.",
        title="UNIQUBE — Sync Panel Selection",
    )


main()
