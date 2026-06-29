# -*- coding: utf-8 -*-
"""Ungroup panel groups (BIMSF Panel / BIMSF_Panel_ / plain panel name)."""
from pyrevit import revit, DB, forms, script
import panel_utils as pu

doc = revit.doc
logger = script.get_logger()


def main():
    panel_ids = set(pu.map_framing(doc).keys())
    all_groups = (
        DB.FilteredElementCollector(doc).OfClass(DB.Group).ToElements()
    )
    bimsf_groups = {}
    for g in all_groups:
        name = g.Name or ""
        if name.startswith("BIMSF"):
            pid = pu.strip_group_prefix(name)
            if pid:
                bimsf_groups[pid] = g
            continue
        if name in panel_ids:
            bimsf_groups[name] = g

    if not bimsf_groups:
        forms.alert(
            "No panel groups found in this model.",
            title="UNIQUBE",
        )
        return

    sorted_ids = sorted(
        bimsf_groups.keys(),
        key=lambda x: pu.panel_display_name(x).lower(),
    )
    options = [
        "All groups ({})".format(len(sorted_ids))
    ] + [pu.panel_display_name(pid) for pid in sorted_ids]
    display_to_pid = {
        pu.panel_display_name(pid): pid for pid in sorted_ids
    }
    selected = forms.SelectFromList.show(
        options,
        title="UNIQUBE — Select Panel(s) to Ungroup",
        multiselect=True,
        button_name="Ungroup",
    )
    if not selected:
        return

    if any("All groups" in s for s in selected):
        to_ungroup = sorted_ids
    else:
        to_ungroup = [display_to_pid.get(s, s) for s in selected]

    with revit.Transaction("UNIQUBE: Ungroup Panels"):
        count = 0
        for pid in to_ungroup:
            grp = bimsf_groups.get(pid)
            if grp is None:
                continue
            try:
                grp.UngroupMembers()
                count += 1
            except Exception as ex:
                logger.debug("Ungroup error for %s: %s", pid, ex)

    forms.alert(
        "Done.\n\nPanels ungrouped: {}".format(count),
        title="UNIQUBE — Ungroup Panels",
    )


main()
