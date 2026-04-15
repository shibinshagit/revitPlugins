# -*- coding: utf-8 -*-
"""Group framing + MEP by BIMSF_Container, color panels, red-mark crossings.
Supports single/multiple panel selection and linked models."""
import random

from pyrevit import revit, DB, forms, script
from System.Collections.Generic import List
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

    all_pids = set(panel_elements.keys()) | set(link_zones.keys())
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
        # Cleanup existing BIMSF groups for selected panels
        all_groups = (
            DB.FilteredElementCollector(doc).OfClass(DB.Group).ToElements()
        )
        for g in all_groups:
            for pid in selected:
                if g.Name == "BIMSF_Panel_" + str(pid):
                    try:
                        doc.Delete(g.Id)
                    except Exception:
                        pass

        mep_assignments = pu.assign_mep_to_panels(
            doc, panel_elements, link_zones
        )

        fill_pattern = (
            DB.FilteredElementCollector(doc)
            .OfClass(DB.FillPatternElement)
            .FirstElement()
        )

        red_settings = DB.OverrideGraphicSettings()
        empty_settings = DB.OverrideGraphicSettings()
        if fill_pattern:
            red_settings.SetSurfaceForegroundPatternId(fill_pattern.Id)
            red_settings.SetSurfaceForegroundPatternColor(DB.Color(255, 0, 0))

        group_count = 0
        crossing_count = 0

        for pid in selected:
            elements = panel_elements.get(pid, [])
            r = random.randint(0, 180)
            g_ = random.randint(50, 255)
            b = random.randint(50, 255)
            p_color = DB.Color(r, g_, b)

            p_settings = DB.OverrideGraphicSettings()
            if fill_pattern:
                p_settings.SetSurfaceForegroundPatternId(fill_pattern.Id)
                p_settings.SetSurfaceForegroundPatternColor(p_color)

            group_ids = List[DB.ElementId]()
            for el in elements:
                view.SetElementOverrides(el.Id, p_settings)
                group_ids.Add(el.Id)

            for eid, pids in mep_assignments.items():
                el = doc.GetElement(eid)
                if el is None:
                    continue
                p_param = el.LookupParameter(pu.PARAM_NAME)

                if len(pids) == 1 and list(pids)[0] == pid:
                    group_ids.Add(eid)
                    view.SetElementOverrides(eid, p_settings)
                    if p_param and not p_param.IsReadOnly:
                        p_param.Set(pid)
                elif len(pids) > 1 and pid in pids:
                    view.SetElementOverrides(eid, red_settings)
                    crossing_count += 1
                    if p_param and not p_param.IsReadOnly:
                        p_param.Set("")

            if group_ids.Count > 1:
                try:
                    new_grp = doc.Create.NewGroup(group_ids)
                    new_grp.GroupType.Name = "BIMSF_Panel_" + str(pid)
                    group_count += 1
                except Exception:
                    pass

    forms.alert(
        "Done.\n\n"
        "Panels processed: {}\n"
        "Groups created: {}\n"
        "Crossing MEP elements (red): {}".format(
            len(selected), group_count, crossing_count
        ),
        title="UNIQUBE — Panel Combine",
    )


main()
