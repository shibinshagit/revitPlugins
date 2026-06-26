# -*- coding: utf-8 -*-
"""Select one panel's MEP group + linked panel framing together."""
from pyrevit import revit, DB, forms, script
import panel_utils as pu

doc = revit.doc
uidoc = revit.uidoc
logger = script.get_logger()


def main():
    if not hasattr(pu, "select_panel_pair"):
        forms.alert("panel_utils.py is out of date — git pull.", title="UNIQUBE")
        return

    link_framing = pu.map_link_framing_by_container(doc)
    panel_elements = pu.map_framing(doc)
    link_zones = pu.map_framing_from_links(doc)
    all_pids = pu.get_all_panel_ids(panel_elements, link_zones)
    all_pids.update(link_framing.keys())

    host_groups = {}
    for g in DB.FilteredElementCollector(doc).OfClass(DB.Group).ToElements():
        for pid in all_pids:
            if pu.group_matches_panel(g.Name, pid):
                host_groups[pu.panel_display_name(pid)] = pid

    if not host_groups and not link_framing:
        forms.alert(
            "No panel groups found.\n\n"
            "Run MEP Group Panels first.",
            title="UNIQUBE — Select Panel + MEP",
        )
        return

    options = sorted(host_groups.keys())
    if not options:
        options = sorted(
            pu.panel_display_name(p) for p in all_pids
        )

    pick = forms.SelectFromList.show(
        options,
        title="UNIQUBE — Select Panel + MEP",
        button_name="Select",
    )
    if not pick:
        return

    pid = host_groups.get(pick)
    if not pid:
        for p in all_pids:
            if pu.panel_display_name(p) == pick:
                pid = p
                break

    if not pid:
        return

    count = pu.select_panel_pair(uidoc, doc, pid, link_framing)
    if count == 0:
        forms.alert(
            "Nothing selected for '{}'.".format(pick),
            title="UNIQUBE",
        )


main()
