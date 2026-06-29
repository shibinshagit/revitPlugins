# -*- coding: utf-8 -*-
"""Group framing + MEP by BIMSF_Container, color panels, red-mark crossings.
Supports single/multiple panel selection and linked models."""
from pyrevit import revit, DB, forms, script
import panel_utils as pu

doc = revit.doc
view = doc.ActiveView
logger = script.get_logger()


def main():
    if isinstance(view, DB.ViewSheet):
        forms.alert("Open a model view, not a sheet.", title="UNIQUBE")
        return

    panel_elements = pu.map_framing(doc)
    link_zones = pu.map_framing_from_links(doc)
    link_framing = pu.map_link_framing_by_container(doc)

    all_pids = set(panel_elements.keys()) | set(link_zones.keys())
    all_pids.update(link_framing.keys())
    if not all_pids:
        forms.alert(
            "No structural framing with '{}' found in host or links.".format(
                pu.PARAM_NAME
            ),
            title="UNIQUBE",
        )
        return

    selected = pu.choose_panels(all_pids)
    if not selected:
        return

    with revit.Transaction("UNIQUBE: Panel Combine (Color)"):
        all_groups = (
            DB.FilteredElementCollector(doc).OfClass(DB.Group).ToElements()
        )
        for g in all_groups:
            for pid in selected:
                if pu.group_matches_panel(g.Name, pid):
                    try:
                        doc.Delete(g.Id)
                    except Exception:
                        pass

        stats = pu.combine_panels_group_color(
            doc,
            view,
            selected,
            panel_elements,
            link_zones,
            link_framing=link_framing,
            tag_mep=True,
        )

    forms.alert(
        "Done.\n\n"
        "Panels processed: {}\n"
        "Groups created: {}\n"
        "Crossing elements (red): {}\n"
        "Linked panel framing colored: {}\n"
        "Host MEP tagged: {}".format(
            len(selected),
            stats["groups"],
            stats["crossing_count"],
            stats["link_framing_colored"],
            stats["mep_tagged"],
        ),
        title="UNIQUBE — Panel Combine",
    )


main()
