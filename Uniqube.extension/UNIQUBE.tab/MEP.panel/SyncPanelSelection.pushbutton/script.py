# -*- coding: utf-8 -*-
"""Toggle panel groups — ungroup for editing, regroup when done."""
from pyrevit import revit, DB, forms, script
import panel_selection_sync as pss
import panel_utils as pu

doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView
logger = script.get_logger()


def main():
    if isinstance(view, DB.ViewSheet):
        forms.alert("Open a model view, not a sheet.", title="UNIQUBE")
        return

    if not hasattr(pu, "ungroup_panels_in_host"):
        forms.alert(
            "panel_utils.py is out of date.\n\n"
            "git pull revitPlugins, then pyRevit → Reload.",
            title="UNIQUBE",
        )
        return

    try:
        removed = pss.purge_legacy_idling(uidoc.Application)
        if removed:
            logger.debug("purged %s legacy idling handler(s)", removed)
    except Exception as ex:
        logger.debug("idling purge failed: %s", ex)

    try:
        panel_ids = pu.discover_host_panel_ids(doc)
        group_count = pu.count_panel_groups(doc, panel_ids)
    except Exception as ex:
        logger.debug("panel state check failed: %s", ex)
        forms.alert(
            "Could not read panel state:\n{}".format(ex),
            title="UNIQUBE",
        )
        return

    if not panel_ids:
        forms.alert(
            "No host panels found.\n\n"
            "Run Prepare MEP Panels first.",
            title="UNIQUBE — Sync Panel Selection",
        )
        return

    if group_count > 0:
        turn_off = forms.alert(
            "Panels are currently GROUPED ({0} group(s)).\n\n"
            "Each panel (framing + MEP) selects as one Revit group.\n\n"
            "Ungroup for individual element selection?".format(group_count),
            yes=True,
            no=True,
            title="UNIQUBE — Sync Panel Selection",
        )
        if not turn_off:
            return
        try:
            with revit.Transaction("UNIQUBE: Ungroup Panels"):
                stats = pss.ungroup_panels(uidoc, view)
        except Exception as ex:
            forms.alert("Ungroup failed:\n{}".format(ex), title="UNIQUBE")
            return
    forms.alert(
        "Panels are now UNGROUPED.\n\n"
        "Groups dissolved: {}\n\n"
        "Select individual studs and pipes. "
        "Click this button again to regroup panel + MEP.\n\n"
        "If selection still jumps to framing after a few seconds, "
        "close and reopen Revit once (clears old auto-select).".format(
            stats.get("ungrouped", 0)
        ),
        title="UNIQUBE — Sync Panel Selection",
    )
        return

    turn_on = forms.alert(
        "Panels are currently UNGROUPED.\n\n"
        "Individual studs and pipes can be selected.\n\n"
        "Regroup panel + MEP back together?",
        yes=True,
        no=True,
        title="UNIQUBE — Sync Panel Selection",
    )
    if not turn_on:
        return
    try:
        with revit.Transaction("UNIQUBE: Regroup Panels"):
            stats = pss.regroup_panels(uidoc, view)
    except Exception as ex:
        forms.alert("Regroup failed:\n{}".format(ex), title="UNIQUBE")
        return
    forms.alert(
        "Panels are now GROUPED.\n\n"
        "Host groups created: {}\n\n"
        "Whole panel + MEP selects together again.".format(
            stats.get("groups", 0)
        ),
        title="UNIQUBE — Sync Panel Selection",
    )


main()
