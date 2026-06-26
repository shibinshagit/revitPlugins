# -*- coding: utf-8 -*-
"""Toggle panel groups — ungroup for editing, regroup when done."""
from pyrevit import revit, DB, forms, script
import panel_selection_sync as pss
import panel_utils as pu

doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView
logger = script.get_logger()


def _link_warning(doc):
    if not hasattr(pu, "get_framing_link_names"):
        return None
    names = pu.get_framing_link_names(doc)
    if not names:
        return None
    return (
        "Structural link still loaded:\n  {0}\n\n"
        "Remove the link (Manage Links → Remove) so clicks stay on "
        "host elements. Status bar must NOT say LINK.".format(
            "\n  ".join(names[:4])
        )
    )


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
        pss.purge_legacy_idling(uidoc.Application)
    except Exception as ex:
        logger.debug("idling purge failed: %s", ex)

    try:
        panel_ids = pu.discover_host_panel_ids(doc)
        group_count = pu.count_panel_groups(doc, panel_ids)
    except Exception as ex:
        forms.alert("Could not read panel state:\n{}".format(ex), title="UNIQUBE")
        return

    if not panel_ids:
        forms.alert(
            "No host panels found.\n\nRun Prepare MEP Panels first.",
            title="UNIQUBE — Sync Panel Selection",
        )
        return

    link_msg = _link_warning(doc)

    if group_count > 0:
        prompt = (
            "Panels are GROUPED ({0} group(s)).\n\n"
            "Ungroup so you can select individual studs and pipes?\n\n"
            "No auto-selection runs when ungrouped.".format(group_count)
        )
        if link_msg:
            prompt = link_msg + "\n\n" + prompt
        if not forms.alert(prompt, yes=True, no=True, title="UNIQUBE — Sync Panel Selection"):
            return
        try:
            with revit.Transaction("UNIQUBE: Ungroup Panels"):
                stats = pss.ungroup_panels(uidoc, view)
        except Exception as ex:
            forms.alert("Ungroup failed:\n{}".format(ex), title="UNIQUBE")
            return
        msg = (
            "Panels are UNGROUPED.\n\n"
            "Groups dissolved: {0}\n"
            "Assemblies disassembled: {1}\n\n"
            "Normal Revit selection only — nothing auto-jumps.\n"
            "Click this button again to regroup.".format(
                stats.get("ungrouped", 0),
                stats.get("disassembled", 0),
            )
        )
        if link_msg:
            msg += "\n\n" + link_msg
        forms.alert(msg, title="UNIQUBE — Sync Panel Selection")
        return

    prompt = "Panels are UNGROUPED.\n\nRegroup panel + MEP together?"
    if link_msg:
        prompt = link_msg + "\n\n" + prompt
    if not forms.alert(prompt, yes=True, no=True, title="UNIQUBE — Sync Panel Selection"):
        return
    try:
        with revit.Transaction("UNIQUBE: Regroup Panels"):
            stats = pss.regroup_panels(uidoc, view)
    except Exception as ex:
        forms.alert("Regroup failed:\n{}".format(ex), title="UNIQUBE")
        return
    forms.alert(
        "Panels are GROUPED.\n\n"
        "Host groups created: {}.".format(stats.get("groups", 0)),
        title="UNIQUBE — Sync Panel Selection",
    )


main()
