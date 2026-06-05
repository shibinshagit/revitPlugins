# -*- coding: utf-8 -*-
"""Assign global sequential Advance Position IDs to structural framing.

Members with the same fingerprint (family type + length + BIMSF_Data)
receive the same ID. IDs are global across the entire project.
Includes a Reset option to revert previous assignments.
"""
from pyrevit import revit, DB, forms, script
import panel_utils as pu

doc = revit.doc
logger = script.get_logger()

POSITION_PARAM = "Advanced Position ID"
TOLERANCE_FT = 0.016  # ~5mm in feet


def _round_to_tol(value):
    """Round a float to the nearest tolerance step."""
    step = TOLERANCE_FT
    return round(value / step) * step


def _get_param_str(elem, param_name):
    """Return string value of a parameter or empty string."""
    p = elem.LookupParameter(param_name)
    if p and p.HasValue:
        return p.AsString() or ""
    return ""


def _get_family_type_name(elem):
    """Get the full family type name like 'BIMSF-SSMA-S 600S162-43'."""
    elem_type = doc.GetElement(elem.GetTypeId())
    if elem_type is None:
        return ""
    fam_name = elem_type.get_Parameter(
        DB.BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM
    )
    type_name = elem_type.get_Parameter(
        DB.BuiltInParameter.SYMBOL_NAME_PARAM
    )
    fam = fam_name.AsString() if fam_name and fam_name.HasValue else ""
    typ = type_name.AsString() if type_name and type_name.HasValue else ""
    return "{} {}".format(fam, typ).strip()


def get_max_existing_id(doc):
    """Scan all structural framing for the highest existing Position ID."""
    max_id = 0
    all_framing = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for elem in all_framing:
        val = _get_param_str(elem, POSITION_PARAM)
        if val:
            try:
                num = int(val)
                if num > max_id:
                    max_id = num
            except (ValueError, TypeError):
                pass
    return max_id


def is_panel_done(elements):
    """True if ALL elements in the panel already have a Position ID."""
    if not elements:
        return True
    for elem in elements:
        val = _get_param_str(elem, POSITION_PARAM)
        if not val:
            return False
    return True


def get_element_length(elem):
    """Get curve length rounded to tolerance."""
    loc = elem.Location
    if isinstance(loc, DB.LocationCurve):
        return _round_to_tol(loc.Curve.Length)
    return 0.0


def compute_fingerprint(elem):
    """Compute the identity fingerprint for a structural member.

    Fingerprint = (family_type_name, rounded_length, bimsf_data)
    Two members are the same "instance" only if all three match.
    - Type determines the profile/gauge
    - Length determines the cut size
    - BIMSF_Data encodes punch/dimple configuration from Vertex BD
    """
    type_name = _get_family_type_name(elem)
    length = get_element_length(elem)
    bimsf_data = _get_param_str(elem, "BIMSF_Data")
    return (type_name, length, bimsf_data)


# --------------- RESET MODE ---------------

def run_reset():
    """Clear Advanced Position IDs from selected panels."""
    panel_elements = pu.map_framing(doc)

    if not panel_elements:
        forms.alert(
            "No structural framing with '{}' found.".format(pu.PARAM_NAME),
            title="UNIQUBE",
        )
        return

    # Find panels that have at least one assigned element
    assigned_panels = {}
    for pid, elements in panel_elements.items():
        for elem in elements:
            if _get_param_str(elem, POSITION_PARAM):
                assigned_panels[pid] = elements
                break

    if not assigned_panels:
        forms.alert(
            "No panels have Position IDs to reset.",
            title="UNIQUBE",
        )
        return

    selected = pu.choose_panels(assigned_panels.keys())
    if not selected:
        return

    cleared = 0
    with revit.Transaction("UNIQUBE: Reset Position IDs"):
        for pid in selected:
            elements = assigned_panels.get(pid, [])
            for elem in elements:
                param = elem.LookupParameter(POSITION_PARAM)
                if param and not param.IsReadOnly and param.HasValue:
                    param.Set("")
                    cleared += 1

    forms.alert(
        "Reset complete.\n\n"
        "Panels reset: {}\n"
        "Members cleared: {}".format(len(selected), cleared),
        title="UNIQUBE — Reset Position IDs",
    )


# --------------- ASSIGN MODE ---------------

def run_assign():
    """Assign Position IDs to selected panels."""
    panel_elements = pu.map_framing(doc)

    if not panel_elements:
        forms.alert(
            "No structural framing with '{}' found.".format(pu.PARAM_NAME),
            title="UNIQUBE",
        )
        return

    # Filter out panels already fully assigned
    pending_panels = {}
    for pid, elements in panel_elements.items():
        if not is_panel_done(elements):
            pending_panels[pid] = elements

    if not pending_panels:
        forms.alert(
            "All panels already have Advanced Position IDs assigned.\n\n"
            "Use 'Reset Position IDs' to clear and re-run.",
            title="UNIQUBE",
        )
        return

    selected = pu.choose_panels(pending_panels.keys())
    if not selected:
        return

    # Get current max ID in the project for global continuity
    current_max = get_max_existing_id(doc)
    next_id = current_max + 1

    # Build fingerprint-to-ID map from already-assigned elements globally
    fingerprint_map = {}
    for pid, elements in panel_elements.items():
        for elem in elements:
            existing_id = _get_param_str(elem, POSITION_PARAM)
            if existing_id:
                try:
                    fp = compute_fingerprint(elem)
                    existing_num = int(existing_id)
                    if fp not in fingerprint_map:
                        fingerprint_map[fp] = existing_num
                except (ValueError, TypeError):
                    pass

    # Process selected panels
    assigned_count = 0
    instance_count = 0

    with revit.Transaction("UNIQUBE: Advance Position ID"):
        for pid in selected:
            elements = pending_panels.get(pid, [])
            if not elements:
                continue

            for elem in elements:
                if _get_param_str(elem, POSITION_PARAM):
                    continue

                fp = compute_fingerprint(elem)

                if fp not in fingerprint_map:
                    fingerprint_map[fp] = next_id
                    next_id += 1
                    instance_count += 1

                pos_id = fingerprint_map[fp]
                param = elem.LookupParameter(POSITION_PARAM)
                if param and not param.IsReadOnly:
                    param.Set(str(pos_id))
                    assigned_count += 1

    forms.alert(
        "Done.\n\n"
        "Panels processed: {}\n"
        "Members assigned: {}\n"
        "Unique instances found: {}\n"
        "ID range: {} - {}".format(
            len(selected),
            assigned_count,
            instance_count,
            current_max + 1 if instance_count else 0,
            next_id - 1 if instance_count else 0,
        ),
        title="UNIQUBE — Advance Position ID",
    )


# --------------- MAIN ---------------

def main():
    action = forms.CommandSwitchWindow.show(
        ["Assign Position IDs", "Reset Position IDs"],
        message="Choose action:",
    )
    if not action:
        return

    if action == "Assign Position IDs":
        run_assign()
    elif action == "Reset Position IDs":
        run_reset()


main()
