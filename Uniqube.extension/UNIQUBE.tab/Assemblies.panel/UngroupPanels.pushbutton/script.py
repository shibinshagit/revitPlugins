# -*- coding: utf-8 -*-
"""Ungroup BIMSF_Panel_ groups. Pick single, multiple, or all."""
from pyrevit import revit, DB, forms, script

doc = revit.doc
logger = script.get_logger()


def main():
    all_groups = (
        DB.FilteredElementCollector(doc).OfClass(DB.Group).ToElements()
    )
    bimsf_groups = {}
    for g in all_groups:
        if g.Name.startswith("BIMSF_Panel_"):
            pid = g.Name.replace("BIMSF_Panel_", "")
            bimsf_groups[pid] = g

    if not bimsf_groups:
        forms.alert(
            "No BIMSF_Panel_ groups found in this model.",
            title="UNIQUBE",
        )
        return

    sorted_ids = sorted(bimsf_groups.keys())
    options = ["All groups ({})".format(len(sorted_ids))] + sorted_ids
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
        to_ungroup = selected

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
