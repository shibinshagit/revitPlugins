# -*- coding: utf-8 -*-
"""Group structural framing + MEP into Revit Groups by BIMSF_Container.
Choose single panel, multiple panels, or all. Supports linked models."""
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
            "No structural framing with '{}' found in host or links.".format(
                pu.PARAM_NAME
            ),
            title="UNIQUBE",
        )
        return

    selected = pu.choose_panels(all_pids)
    if not selected:
        return

    with revit.Transaction("UNIQUBE: Group Panels"):
        # Remove existing groups for selected panels first
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

        group_count = 0
        for pid in selected:
            elements = panel_elements.get(pid, [])
            group_ids = List[DB.ElementId]()
            for el in elements:
                group_ids.Add(el.Id)

            for eid, pids in mep_assignments.items():
                if len(pids) == 1 and list(pids)[0] == pid:
                    el = doc.GetElement(eid)
                    group_ids.Add(eid)
                    p_param = el.LookupParameter(pu.PARAM_NAME)
                    if p_param and not p_param.IsReadOnly:
                        p_param.Set(pid)

            if group_ids.Count > 1:
                try:
                    new_grp = doc.Create.NewGroup(group_ids)
                    new_grp.GroupType.Name = "BIMSF_Panel_" + str(pid)
                    group_count += 1
                except Exception as ex:
                    logger.debug("Group error for %s: %s", pid, ex)

    forms.alert(
        "Done.\n\nPanels selected: {}\nGroups created: {}".format(
            len(selected), group_count
        ),
        title="UNIQUBE — Group Panels",
    )


main()
