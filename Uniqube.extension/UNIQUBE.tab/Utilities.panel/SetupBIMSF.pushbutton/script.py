# -*- coding: utf-8 -*-
"""Add BIMSF_Container to Vertex BD / IFC-imported structural elements.

Workflow:
1. If framing is still in a link → tells user to Bind Link first.
2. Adds BIMSF_Container as a project parameter on relevant categories.
3. Scans framing/columns for elements without BIMSF_Container values.
4. Offers auto-assign (from IfcTag, Mark, Assembly name) or manual selection.
"""
from pyrevit import revit, DB, forms, script
from System.Collections.Generic import List
import re

doc = revit.doc
logger = script.get_logger()

PARAM_NAME = "BIMSF_Container"

TARGET_CATS = [
    DB.BuiltInCategory.OST_StructuralFraming,
    DB.BuiltInCategory.OST_StructuralColumns,
    DB.BuiltInCategory.OST_GenericModel,
]

# IFC / Vertex BD properties that might contain a panel or frame identifier
SOURCE_PARAMS = [
    "IfcTag",
    "IfcName",
    "Mark",
    "Assembly Code",
    "Assembly Name",
    "MasterContainer",
    "Type Name",
]


def _has_links_with_framing():
    """Check if there are linked models with structural framing."""
    links = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.RevitLinkInstance)
        .ToElements()
    )
    for link in links:
        ld = link.GetLinkDocument()
        if ld is None:
            continue
        col = (
            DB.FilteredElementCollector(ld)
            .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
            .WhereElementIsNotElementType()
        )
        if col.GetElementCount() > 0:
            return True
        col2 = (
            DB.FilteredElementCollector(ld)
            .OfCategory(DB.BuiltInCategory.OST_StructuralColumns)
            .WhereElementIsNotElementType()
        )
        if col2.GetElementCount() > 0:
            return True
    return False


def _ensure_bimsf_parameter(doc):
    """Add BIMSF_Container as a project text parameter if it doesn't exist yet.

    Returns True if the parameter already exists or was created.
    """
    # Check if any element already has the parameter
    test_col = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
        .WhereElementIsNotElementType()
    )
    for el in test_col:
        if el.LookupParameter(PARAM_NAME) is not None:
            return True

    test_col2 = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_StructuralColumns)
        .WhereElementIsNotElementType()
    )
    for el in test_col2:
        if el.LookupParameter(PARAM_NAME) is not None:
            return True

    # Parameter doesn't exist — need to create it
    # Try using the DefinitionFile approach (shared parameter)
    app = doc.Application
    orig_file = app.SharedParametersFilename

    import os
    import tempfile
    temp_sp = os.path.join(tempfile.gettempdir(), "UNIQUBE_shared_params.txt")
    if not os.path.exists(temp_sp):
        with open(temp_sp, "w") as f:
            f.write("")

    try:
        app.SharedParametersFilename = temp_sp
        sp_file = app.OpenSharedParameterFile()
        if sp_file is None:
            forms.alert(
                "Cannot open/create shared parameter file.\n"
                "Please add '{}' manually via Project Parameters.".format(
                    PARAM_NAME
                ),
                title="UNIQUBE",
            )
            return False

        # Get or create group
        grp = sp_file.Groups.get_Item("UNIQUBE")
        if grp is None:
            grp = sp_file.Groups.Create("UNIQUBE")

        # Get or create definition
        ext_def = grp.Definitions.get_Item(PARAM_NAME)
        if ext_def is None:
            opts = DB.ExternalDefinitionCreationOptions(
                PARAM_NAME, DB.SpecTypeId.String.Text
            )
            ext_def = grp.Definitions.Create(opts)

        # Bind to categories
        cat_set = app.Create.NewCategorySet()
        for bic in TARGET_CATS:
            cat = doc.Settings.Categories.get_Item(bic)
            if cat is not None:
                cat_set.Insert(cat)

        binding = app.Create.NewInstanceBinding(cat_set)
        doc.ParameterBindings.Insert(ext_def, binding)
        return True

    except Exception as ex:
        logger.debug("Parameter creation error: %s", ex)
        forms.alert(
            "Could not auto-create '{}' parameter.\n\n"
            "Error: {}\n\n"
            "Please add it manually:\n"
            "Manage → Project Parameters → Add → Text → "
            "assign to Structural Framing + Structural Columns.".format(
                PARAM_NAME, ex
            ),
            title="UNIQUBE",
        )
        return False
    finally:
        if orig_file:
            app.SharedParametersFilename = orig_file


def _collect_untagged_elements():
    """Return elements in target categories that have empty BIMSF_Container."""
    result = []
    for bic in TARGET_CATS:
        col = (
            DB.FilteredElementCollector(doc)
            .OfCategory(bic)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        for el in col:
            p = el.LookupParameter(PARAM_NAME)
            if p is None:
                result.append(el)
            elif not p.HasValue or not p.AsString():
                result.append(el)
    return result


def _guess_panel_id(el):
    """Try to extract a panel/frame identifier from IFC or other properties."""
    for pname in SOURCE_PARAMS:
        p = el.LookupParameter(pname)
        if p and p.HasValue:
            val = p.AsString()
            if val:
                return val.strip()

    # Try element Type name
    etype = doc.GetElement(el.GetTypeId())
    if etype is not None:
        tname = etype.Name
        if tname:
            return tname.strip()

    return None


def _extract_panel_prefix(raw_id):
    """Try to extract a meaningful panel name from a raw ID.

    Vertex BD often uses names like 'I Stud 6005162-43 50ksi'
    or tags like '400082'. We try to find a frame/panel pattern.
    """
    if not raw_id:
        return None
    # If it looks like *ELB-001 or FP-101 style, keep it
    if raw_id.startswith("*") or re.match(r"^[A-Z]{1,4}[-_]\d+", raw_id):
        return raw_id
    return raw_id


def main():
    # Step 0: Check for linked framing
    has_link_framing = _has_links_with_framing()

    # Check for native framing
    native_framing = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
        .WhereElementIsNotElementType()
        .GetElementCount()
    )
    native_columns = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_StructuralColumns)
        .WhereElementIsNotElementType()
        .GetElementCount()
    )

    if native_framing == 0 and native_columns == 0:
        if has_link_framing:
            forms.alert(
                "Structural framing was found only in LINKED models.\n\n"
                "To add BIMSF_Container parameters, you must first convert "
                "the linked elements to native Revit elements:\n\n"
                "1. Select the link instance in the view\n"
                "2. Go to Manage tab → Manage Links\n"
                "   OR right-click the link → Bind Link\n"
                "3. Choose 'Bind Link' (this copies elements into your model)\n"
                "4. Then run this tool again\n\n"
                "Note: Binding is non-reversible. Save a backup first.",
                title="UNIQUBE — Setup BIMSF",
            )
        else:
            forms.alert(
                "No structural framing or columns found in this model.",
                title="UNIQUBE",
            )
        return

    # Step 1: Ensure BIMSF_Container parameter exists
    with revit.Transaction("UNIQUBE: Add BIMSF_Container parameter"):
        param_ok = _ensure_bimsf_parameter(doc)

    if not param_ok:
        return

    # Step 2: Find untagged elements
    untagged = _collect_untagged_elements()
    if not untagged:
        forms.alert(
            "All structural elements already have BIMSF_Container values.\n"
            "Nothing to do.",
            title="UNIQUBE",
        )
        return

    # Step 3: Show options
    mode = forms.CommandSwitchWindow.show(
        ["Auto-assign from element properties", "Manual — enter panel ID for selection"],
        message="Found {} elements without BIMSF_Container.\nHow to assign?".format(
            len(untagged)
        ),
    )
    if not mode:
        return

    if "Auto" in mode:
        # Auto-assign
        assigned = 0
        skipped = 0
        panel_ids_found = set()

        with revit.Transaction("UNIQUBE: Auto-assign BIMSF_Container"):
            for el in untagged:
                raw = _guess_panel_id(el)
                pid = _extract_panel_prefix(raw)
                if pid:
                    p = el.LookupParameter(PARAM_NAME)
                    if p and not p.IsReadOnly:
                        p.Set(pid)
                        assigned += 1
                        panel_ids_found.add(pid)
                    else:
                        skipped += 1
                else:
                    skipped += 1

        msg = (
            "Auto-assignment complete.\n\n"
            "Assigned: {} elements\n"
            "Skipped (no source data): {}\n"
            "Unique panel IDs found: {}".format(
                assigned, skipped, len(panel_ids_found)
            )
        )
        if panel_ids_found:
            samples = sorted(panel_ids_found)[:10]
            msg += "\n\nSample IDs: {}".format(", ".join(samples))
        if skipped > 0:
            msg += (
                "\n\nFor skipped elements, use 'Manual' mode or "
                "edit BIMSF_Container in a schedule."
            )
        forms.alert(msg, title="UNIQUBE — Auto-assign")

    else:
        # Manual mode: let user type a panel ID, then select elements
        panel_id = forms.ask_for_string(
            prompt="Enter the panel ID to assign (e.g. *ELB-001):",
            title="UNIQUBE — Manual BIMSF",
        )
        if not panel_id:
            return

        forms.alert(
            "After clicking OK, select the structural elements in the view "
            "that belong to panel '{}'.\n\n"
            "Then press Finish (green check) in the ribbon.".format(panel_id),
            title="UNIQUBE",
        )

        try:
            sel_refs = revit.uidoc.Selection.PickObjects(
                DB.Selection.ObjectType.Element,
                "Select elements for panel '{}'".format(panel_id),
            )
        except Exception:
            return

        if not sel_refs:
            return

        with revit.Transaction(
            "UNIQUBE: Manual assign '{}'".format(panel_id)
        ):
            count = 0
            for ref in sel_refs:
                el = doc.GetElement(ref.ElementId)
                if el is None:
                    continue
                p = el.LookupParameter(PARAM_NAME)
                if p and not p.IsReadOnly:
                    p.Set(panel_id)
                    count += 1

        forms.alert(
            "Assigned '{}' to {} element(s).".format(panel_id, count),
            title="UNIQUBE",
        )


main()
