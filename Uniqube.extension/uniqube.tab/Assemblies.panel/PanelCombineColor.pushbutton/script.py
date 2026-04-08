# -*- coding: utf-8 -*-
"""Group framing + MEP by BIMSF_Container, color panels, red-mark crossings."""
import random

from pyrevit import revit, DB, forms, script
from System.Collections.Generic import List

doc = revit.doc
view = doc.ActiveView
logger = script.get_logger()

PARAM_NAME = "BIMSF_Container"

MEP_CATS = [
    DB.BuiltInCategory.OST_Conduit,
    DB.BuiltInCategory.OST_ConduitFitting,
    DB.BuiltInCategory.OST_PipeCurves,
    DB.BuiltInCategory.OST_PipeFitting,
    DB.BuiltInCategory.OST_PipeInsulations,
    DB.BuiltInCategory.OST_ElectricalFixtures,
]


def main():
    if isinstance(view, DB.ViewSheet):
        forms.alert("Open a model view, not a sheet.", title="uniqube")
        return

    with revit.Transaction("uniqube: Panel Combine (Color)"):
        # 1. Cleanup existing BIMSF groups
        all_groups = (
            DB.FilteredElementCollector(doc)
            .OfClass(DB.Group)
            .ToElements()
        )
        for g in all_groups:
            if g.Name.startswith("BIMSF_Panel_"):
                try:
                    doc.Delete(g.Id)
                except Exception:
                    pass

        # 2. Map framing by BIMSF_Container
        all_framing = (
            DB.FilteredElementCollector(doc)
            .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        panel_elements = {}
        panel_colors = {}
        fill_pattern = (
            DB.FilteredElementCollector(doc)
            .OfClass(DB.FillPatternElement)
            .FirstElement()
        )

        for beam in all_framing:
            p_param = beam.LookupParameter(PARAM_NAME)
            if p_param and p_param.HasValue:
                pid = p_param.AsString()
                if not pid:
                    continue
                if pid not in panel_elements:
                    panel_elements[pid] = []
                    r = random.randint(0, 180)
                    g_ = random.randint(50, 255)
                    b = random.randint(50, 255)
                    panel_colors[pid] = DB.Color(r, g_, b)
                panel_elements[pid].append(beam)

        if not panel_elements:
            forms.alert(
                "No structural framing with '{}' found.".format(PARAM_NAME),
                title="uniqube",
            )
            return

        # 3. Collect all MEP elements
        mep_filter = DB.ElementMulticategoryFilter(
            List[DB.BuiltInCategory](MEP_CATS)
        )
        all_mep = (
            DB.FilteredElementCollector(doc)
            .WherePasses(mep_filter)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        mep_assignments = {}
        for item in all_mep:
            mep_assignments[item.Id] = set()

        # 4. Spatial check — bounding box of each panel zone
        for pid, elements in panel_elements.items():
            min_pt = DB.XYZ(10000, 10000, 10000)
            max_pt = DB.XYZ(-10000, -10000, -10000)
            for el in elements:
                bbox = el.get_BoundingBox(None)
                if bbox:
                    min_pt = DB.XYZ(
                        min(min_pt.X, bbox.Min.X),
                        min(min_pt.Y, bbox.Min.Y),
                        min(min_pt.Z, bbox.Min.Z),
                    )
                    max_pt = DB.XYZ(
                        max(max_pt.X, bbox.Max.X),
                        max(max_pt.Y, bbox.Max.Y),
                        max(max_pt.Z, bbox.Max.Z),
                    )

            zone = DB.Outline(
                min_pt.Add(DB.XYZ(-0.2, -0.2, -0.2)),
                max_pt.Add(DB.XYZ(0.2, 0.2, 0.2)),
            )
            nearby = (
                DB.FilteredElementCollector(doc)
                .WherePasses(mep_filter)
                .WherePasses(DB.BoundingBoxIntersectsFilter(zone))
                .ToElements()
            )
            for item in nearby:
                if item.Id in mep_assignments:
                    mep_assignments[item.Id].add(pid)

        # 5. Apply graphics and grouping
        red_settings = DB.OverrideGraphicSettings()
        empty_settings = DB.OverrideGraphicSettings()
        if fill_pattern:
            red_settings.SetSurfaceForegroundPatternId(fill_pattern.Id)
            red_settings.SetSurfaceForegroundPatternColor(DB.Color(255, 0, 0))

        group_count = 0
        crossing_count = 0

        for pid, elements in panel_elements.items():
            p_color = panel_colors[pid]
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
                p_param = el.LookupParameter(PARAM_NAME)

                if len(pids) == 1 and list(pids)[0] == pid:
                    group_ids.Add(eid)
                    view.SetElementOverrides(eid, p_settings)
                    if p_param and not p_param.IsReadOnly:
                        p_param.Set(pid)
                elif len(pids) > 1:
                    view.SetElementOverrides(eid, red_settings)
                    crossing_count += 1
                    if p_param and not p_param.IsReadOnly:
                        p_param.Set("")
                elif len(pids) == 0:
                    view.SetElementOverrides(eid, empty_settings)
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
        "Panels found: {}\n"
        "Groups created: {}\n"
        "Crossing MEP elements (red): {}".format(
            len(panel_elements), group_count, crossing_count
        ),
        title="uniqube — Panel Combine",
    )


main()
