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
            "When ON, clicking panel or MEP can auto-expand the selection.\n\n"
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
            "Our auto-select tool is stopped.\n\n"
            "If clicking still selects the whole panel: after Prepare MEP "
            "Panels, each panel is a Revit GROUP in the host model. Revit "
            "always selects the whole group when you click one member — "
            "that is not sync.\n\n"
            "To pick one stud or pipe: press Tab to cycle, or right-click "
            "the group → Edit Group.",
            title="UNIQUBE — Sync Panel Selection",
        )
        return

    turn_on = forms.alert(
        "Panel selection sync is currently OFF.\n\n"
        "Turn sync ON?\n\n"
        "Useful when the structural link is still loaded — clicking panel "
        "or MEP auto-selects the full panel + MEP pair.\n\n"
        "After framing is copied to the host and the link is removed, "
        "sync is usually not needed (Revit groups already select together).",
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
