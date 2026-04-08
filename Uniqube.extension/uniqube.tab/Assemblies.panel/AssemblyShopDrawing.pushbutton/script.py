# -*- coding: utf-8 -*-
"""Create assemblies + 3D/plan views for each BIMSF_Container panel."""
from pyrevit import revit, DB, forms, script
from System.Collections.Generic import List

doc = revit.doc
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
    with revit.Transaction("uniqube: Assembly Shop Drawing"):
        # 1. Cleanup
        all_assemblies = (
            DB.FilteredElementCollector(doc)
            .OfClass(DB.AssemblyInstance)
            .ToElements()
        )
        for a in all_assemblies:
            if a.AssemblyTypeName.startswith("BIMSF_Panel_"):
                try:
                    doc.Delete(a.Id)
                except Exception:
                    pass

        # 2. Map framing
        all_framing = (
            DB.FilteredElementCollector(doc)
            .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        panel_elements = {}
        for beam in all_framing:
            p_param = beam.LookupParameter(PARAM_NAME)
            if p_param and p_param.HasValue:
                pid = p_param.AsString()
                if not pid:
                    continue
                if pid not in panel_elements:
                    panel_elements[pid] = []
                panel_elements[pid].append(beam)

        if not panel_elements:
            forms.alert(
                "No structural framing with '{}' found.".format(PARAM_NAME),
                title="uniqube",
            )
            return

        # 3. MEP spatial assignment
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

        # 4. Create assemblies + views
        asm_count = 0
        view_count = 0
        for pid, elements in panel_elements.items():
            assembly_ids = List[DB.ElementId]()
            for el in elements:
                assembly_ids.Add(el.Id)

            for eid, pids in mep_assignments.items():
                if len(pids) == 1 and list(pids)[0] == pid:
                    el = doc.GetElement(eid)
                    assembly_ids.Add(eid)
                    p_param = el.LookupParameter(PARAM_NAME)
                    if p_param and not p_param.IsReadOnly:
                        p_param.Set(pid)

            if assembly_ids.Count > 1:
                try:
                    naming_cat = DB.ElementId(
                        DB.BuiltInCategory.OST_StructuralFraming
                    )
                    new_assembly = DB.AssemblyInstance.Create(
                        doc, assembly_ids, naming_cat
                    )
                    doc.Regenerate()
                    new_assembly.AssemblyTypeName = "BIMSF_Panel_" + str(pid)
                    asm_count += 1

                    view3d = DB.AssemblyViewUtils.Create3DOrthographic(
                        doc, new_assembly.Id
                    )
                    view3d.Name = "3D_" + new_assembly.AssemblyTypeName
                    view_count += 1

                    view_plan = DB.AssemblyViewUtils.CreateDetailView(
                        doc,
                        new_assembly.Id,
                        DB.AssemblyDetailViewOrientation.HorizontalDetail,
                    )
                    view_plan.Name = "Plan_" + new_assembly.AssemblyTypeName
                    view_count += 1
                except Exception as ex:
                    logger.debug("Assembly/view error for %s: %s", pid, ex)

    forms.alert(
        "Done.\n\nPanels: {}\nAssemblies: {}\nViews created: {}".format(
            len(panel_elements), asm_count, view_count
        ),
        title="uniqube — Shop Drawing",
    )


main()
