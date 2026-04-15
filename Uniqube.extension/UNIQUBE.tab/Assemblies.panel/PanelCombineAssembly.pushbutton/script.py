# -*- coding: utf-8 -*-
"""Create Revit assemblies from BIMSF_Container panels (no color).
Supports single/multiple panel selection and linked models."""
from pyrevit import revit, DB, forms, script
from System.Collections.Generic import List
import panel_utils as pu

doc = revit.doc
logger = script.get_logger()


def main():
    panel_elements = pu.map_framing(doc)
    link_zones = pu.map_framing_from_links(doc)

    all_pids = set(panel_elements.keys()) | set(link_zones.keys())
    if not all_pids:
        forms.alert(
            "No structural framing with '{}' found.".format(pu.PARAM_NAME),
            title="UNIQUBE",
        )
        return

    selected = pu.choose_panels(all_pids)
    if not selected:
        return

    with revit.Transaction("UNIQUBE: Panel Combine (Assembly)"):
        all_assemblies = (
            DB.FilteredElementCollector(doc)
            .OfClass(DB.AssemblyInstance)
            .ToElements()
        )
        for a in all_assemblies:
            for pid in selected:
                if a.AssemblyTypeName == "BIMSF_Panel_" + str(pid):
                    try:
                        doc.Delete(a.Id)
                    except Exception:
                        pass

        mep_assignments = pu.assign_mep_to_panels(
            doc, panel_elements, link_zones
        )

        asm_count = 0
        for pid in selected:
            elements = panel_elements.get(pid, [])
            assembly_ids = List[DB.ElementId]()
            for el in elements:
                assembly_ids.Add(el.Id)

            for eid, pids in mep_assignments.items():
                if len(pids) == 1 and list(pids)[0] == pid:
                    el = doc.GetElement(eid)
                    assembly_ids.Add(eid)
                    p_param = el.LookupParameter(pu.PARAM_NAME)
                    if p_param and not p_param.IsReadOnly:
                        p_param.Set(pid)

            if assembly_ids.Count > 1:
                try:
                    naming_cat = DB.ElementId(
                        DB.BuiltInCategory.OST_StructuralFraming
                    )
                    new_asm = DB.AssemblyInstance.Create(
                        doc, assembly_ids, naming_cat
                    )
                    doc.Regenerate()
                    new_asm.AssemblyTypeName = "BIMSF_Panel_" + str(pid)
                    asm_count += 1
                except Exception:
                    pass

    forms.alert(
        "Done.\n\nPanels: {}\nAssemblies created: {}".format(
            len(selected), asm_count
        ),
        title="UNIQUBE — Panel Assembly",
    )


main()
