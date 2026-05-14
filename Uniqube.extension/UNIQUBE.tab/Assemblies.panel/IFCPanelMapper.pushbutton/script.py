# -*- coding: utf-8 -*-
"""Map IFC assembly/panel info into BIMSF_Container for Vertex BD imports.

Searches for panel/assembly identifiers in these IFC parameters (priority order):
  1. IfcDecomposes — parent assembly name
  2. BIMSF_Container (already set, skip)
  3. MasterContainer
  4. IfcElementAssembly name from property sets
  5. IfcTag-based assembly grouping

Works on both opened IFC models and linked IFC (RevitLinkInstance).
"""
from pyrevit import revit, DB, forms, script

doc = revit.doc
logger = script.get_logger()

PARAM_NAME = "BIMSF_Container"

IFC_PANEL_PARAMS = [
    "IfcDecomposes",
    "MasterContainer",
    "BIMSF_Container",
    "Assembly Name",
    "Panel Name",
    "Panel ID",
    "ElementAssembly",
]

STRUCTURAL_CATS = [
    DB.BuiltInCategory.OST_StructuralFraming,
    DB.BuiltInCategory.OST_StructuralColumns,
    DB.BuiltInCategory.OST_Columns,
    DB.BuiltInCategory.OST_GenericModel,
]


def _get_param_value(elem, param_name):
    """Try to get string value from a named parameter."""
    p = elem.LookupParameter(param_name)
    if p and p.HasValue:
        if p.StorageType == DB.StorageType.String:
            val = p.AsString()
            if val and val.strip():
                return val.strip()
        elif p.StorageType == DB.StorageType.Integer:
            return str(p.AsInteger())
    return None


def _find_panel_id(elem):
    """Determine which panel/assembly this element belongs to from IFC params."""
    for pname in IFC_PANEL_PARAMS:
        val = _get_param_value(elem, pname)
        if val:
            if pname == PARAM_NAME:
                return val
            return val
    return None


def _set_bimsf_container(elem, value):
    """Set BIMSF_Container param if it exists and is writable."""
    p = elem.LookupParameter(PARAM_NAME)
    if p and not p.IsReadOnly:
        p.Set(value)
        return True
    return False


def _process_host_elements(doc):
    """Process elements in the host document (opened IFC or host model)."""
    results = {"mapped": 0, "skipped": 0, "no_param": 0, "no_panel": 0}

    for cat in STRUCTURAL_CATS:
        try:
            elements = (
                DB.FilteredElementCollector(doc)
                .OfCategory(cat)
                .WhereElementIsNotElementType()
                .ToElements()
            )
        except Exception:
            continue

        for elem in elements:
            panel_id = _find_panel_id(elem)
            if not panel_id:
                results["no_panel"] += 1
                continue

            existing = _get_param_value(elem, PARAM_NAME)
            if existing == panel_id:
                results["skipped"] += 1
                continue

            if _set_bimsf_container(elem, panel_id):
                results["mapped"] += 1
            else:
                results["no_param"] += 1

    # Also check DirectShape elements (IFC imports often come as DirectShape)
    try:
        directs = (
            DB.FilteredElementCollector(doc)
            .OfClass(DB.DirectShape)
            .ToElements()
        )
        for elem in directs:
            panel_id = _find_panel_id(elem)
            if not panel_id:
                results["no_panel"] += 1
                continue

            existing = _get_param_value(elem, PARAM_NAME)
            if existing == panel_id:
                results["skipped"] += 1
                continue

            if _set_bimsf_container(elem, panel_id):
                results["mapped"] += 1
            else:
                results["no_param"] += 1
    except Exception:
        pass

    return results


def _process_link_elements(doc):
    """Read IFC data from linked models and map to host elements if needed."""
    links = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.RevitLinkInstance)
        .ToElements()
    )
    link_info = {}
    for link_inst in links:
        link_doc = link_inst.GetLinkDocument()
        if link_doc is None:
            continue
        link_name = link_doc.Title
        link_panels = set()

        for cat in STRUCTURAL_CATS:
            try:
                elements = (
                    DB.FilteredElementCollector(link_doc)
                    .OfCategory(cat)
                    .WhereElementIsNotElementType()
                    .ToElements()
                )
            except Exception:
                continue
            for elem in elements:
                pid = _find_panel_id(elem)
                if pid:
                    link_panels.add(pid)

        try:
            directs = (
                DB.FilteredElementCollector(link_doc)
                .OfClass(DB.DirectShape)
                .ToElements()
            )
            for elem in directs:
                pid = _find_panel_id(elem)
                if pid:
                    link_panels.add(pid)
        except Exception:
            pass

        if link_panels:
            link_info[link_name] = sorted(link_panels)

    return link_info


def main():
    choice = forms.CommandSwitchWindow.show(
        ["Map IFC panels in this model (opened IFC)",
         "Show panel info from linked IFC models",
         "Both — map host + show links"],
        message="UNIQUBE — IFC Panel Mapper\n\n"
                "Choose what to process:",
    )
    if not choice:
        return

    results = None
    link_info = None

    if "this model" in choice or "Both" in choice:
        with revit.Transaction("UNIQUBE: IFC Panel Mapper"):
            results = _process_host_elements(doc)

    if "linked" in choice or "Both" in choice:
        link_info = _process_link_elements(doc)

    # Build summary
    msg_parts = []

    if results:
        msg_parts.append(
            "Host model mapping:\n"
            "  Mapped to BIMSF_Container: {mapped}\n"
            "  Already correct (skipped): {skipped}\n"
            "  No BIMSF_Container param on element: {no_param}\n"
            "  No panel/assembly info found: {no_panel}".format(**results)
        )
        if results["no_param"] > 0:
            msg_parts.append(
                "\nTip: Elements without BIMSF_Container parameter "
                "need the shared parameter added first.\n"
                "Use Manage → Project Parameters → Add → BIMSF_Container (Text)."
            )

    if link_info:
        msg_parts.append("\nLinked IFC panel info:")
        for lname, panels in link_info.items():
            msg_parts.append(
                "  {}: {} panels found".format(lname, len(panels))
            )
            if len(panels) <= 20:
                msg_parts.append("    " + ", ".join(panels))
            else:
                msg_parts.append(
                    "    " + ", ".join(panels[:15]) + " ... and more"
                )

    if not msg_parts:
        msg_parts.append("Nothing processed.")

    forms.alert("\n".join(msg_parts), title="UNIQUBE — IFC Panel Mapper")


main()
