# -*- coding: utf-8 -*-
"""Setup BIMSF — auto-tag host MEP elements from linked structural zones.

Works directly with linked models (Vertex BD / IFC) — NO binding required.
Reads panel IDs from linked framing properties (BIMSF_Container, IfcTag,
Mark, etc.), builds spatial zones, and writes BIMSF_Container on host MEP.
"""
from pyrevit import revit, DB, forms, script
from System.Collections.Generic import List
import sys
import os

sys.path.append(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "Assemblies.panel",
        "lib",
    )
)
import panel_utils

doc = revit.doc
logger = script.get_logger()

PARAM_NAME = "BIMSF_Container"

MEP_CATS_BIC = [
    DB.BuiltInCategory.OST_Conduit,
    DB.BuiltInCategory.OST_ConduitFitting,
    DB.BuiltInCategory.OST_PipeCurves,
    DB.BuiltInCategory.OST_PipeFitting,
    DB.BuiltInCategory.OST_PipeInsulations,
    DB.BuiltInCategory.OST_ElectricalFixtures,
    DB.BuiltInCategory.OST_DuctCurves,
    DB.BuiltInCategory.OST_DuctFitting,
    DB.BuiltInCategory.OST_CableTray,
    DB.BuiltInCategory.OST_CableTrayFitting,
]

STRUCT_CATS_BIC = [
    DB.BuiltInCategory.OST_StructuralFraming,
    DB.BuiltInCategory.OST_StructuralColumns,
    DB.BuiltInCategory.OST_GenericModel,
]

ALL_CATS_BIC = MEP_CATS_BIC + STRUCT_CATS_BIC


def _ensure_bimsf_on_mep(doc):
    """Add BIMSF_Container as a project parameter on MEP + structural categories."""
    for bic in ALL_CATS_BIC:
        col = (
            DB.FilteredElementCollector(doc)
            .OfCategory(bic)
            .WhereElementIsNotElementType()
        )
        for el in col:
            if el.LookupParameter(PARAM_NAME) is not None:
                return True
        break

    app = doc.Application
    orig_file = app.SharedParametersFilename

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
                "Please add '{}' manually via Project Parameters.".format(PARAM_NAME),
                title="UNIQUBE",
            )
            return False

        grp = sp_file.Groups.get_Item("UNIQUBE")
        if grp is None:
            grp = sp_file.Groups.Create("UNIQUBE")

        ext_def = grp.Definitions.get_Item(PARAM_NAME)
        if ext_def is None:
            opts = DB.ExternalDefinitionCreationOptions(
                PARAM_NAME, DB.SpecTypeId.String.Text
            )
            ext_def = grp.Definitions.Create(opts)

        cat_set = app.Create.NewCategorySet()
        for bic in ALL_CATS_BIC:
            cat = doc.Settings.Categories.get_Item(bic)
            if cat is not None:
                cat_set.Insert(cat)

        binding = app.Create.NewInstanceBinding(cat_set)
        doc.ParameterBindings.Insert(ext_def, binding)
        return True

    except Exception as ex:
        logger.debug("Parameter creation error: %s", ex)
        forms.alert(
            "Could not auto-create '{}' parameter.\n\nError: {}\n\n"
            "Add it manually: Manage → Project Parameters → Add → Text → "
            "assign to Conduit, Pipes, Electrical Fixtures, etc.".format(
                PARAM_NAME, ex
            ),
            title="UNIQUBE",
        )
        return False
    finally:
        if orig_file:
            app.SharedParametersFilename = orig_file


def _build_zones_from_links():
    """Get panel zones from links using panel_utils (with IFC fallbacks)."""
    return panel_utils.map_framing_from_links(doc)


def _build_zones_from_host():
    """Get panel zones from host framing."""
    pe = panel_utils.map_framing(doc)
    zones = {}
    for pid, elements in pe.items():
        min_pt, max_pt = panel_utils.compute_panel_bbox(elements)
        if pid not in zones:
            zones[pid] = []
        zones[pid].append((min_pt, max_pt))
    return zones


def _merge_zones(z1, z2):
    """Merge two zone dictionaries."""
    merged = dict(z1)
    for pid, bboxes in z2.items():
        if pid not in merged:
            merged[pid] = []
        merged[pid].extend(bboxes)
    return merged


def _compute_zone_bbox(bboxes):
    """Compute a single combined bounding box from a list of (min, max) tuples."""
    min_pt = DB.XYZ(1e9, 1e9, 1e9)
    max_pt = DB.XYZ(-1e9, -1e9, -1e9)
    for bb_min, bb_max in bboxes:
        min_pt = DB.XYZ(
            min(min_pt.X, bb_min.X),
            min(min_pt.Y, bb_min.Y),
            min(min_pt.Z, bb_min.Z),
        )
        max_pt = DB.XYZ(
            max(max_pt.X, bb_max.X),
            max(max_pt.Y, bb_max.Y),
            max(max_pt.Z, bb_max.Z),
        )
    return min_pt, max_pt


def _tag_host_mep(zones):
    """Assign BIMSF_Container to host MEP elements based on panel zones.

    Returns (tagged_count, multi_panel_count, untagged_count).
    """
    mep_filter = panel_utils.get_mep_filter()
    all_mep = (
        DB.FilteredElementCollector(doc)
        .WherePasses(mep_filter)
        .WhereElementIsNotElementType()
        .ToElements()
    )

    mep_panels = {}
    for el in all_mep:
        mep_panels[el.Id] = set()

    for pid, bboxes in zones.items():
        z_min, z_max = _compute_zone_bbox(bboxes)
        outline = DB.Outline(
            z_min.Add(DB.XYZ(-0.2, -0.2, -0.2)),
            z_max.Add(DB.XYZ(0.2, 0.2, 0.2)),
        )
        nearby = (
            DB.FilteredElementCollector(doc)
            .WherePasses(mep_filter)
            .WherePasses(DB.BoundingBoxIntersectsFilter(outline))
            .ToElements()
        )
        for item in nearby:
            if item.Id in mep_panels:
                mep_panels[item.Id].add(pid)

    tagged = 0
    multi = 0
    untagged = 0

    for eid, pids in mep_panels.items():
        el = doc.GetElement(eid)
        if el is None:
            continue
        p = el.LookupParameter(PARAM_NAME)
        if p is None or p.IsReadOnly:
            untagged += 1
            continue
        if len(pids) == 0:
            untagged += 1
        elif len(pids) == 1:
            p.Set(list(pids)[0])
            tagged += 1
        else:
            p.Set("; ".join(sorted(pids)))
            tagged += 1
            multi += 1

    return tagged, multi, untagged


def _tag_host_struct(zones):
    """Also tag host structural elements if they exist and are untagged."""
    count = 0
    for bic in [DB.BuiltInCategory.OST_StructuralFraming,
                DB.BuiltInCategory.OST_StructuralColumns]:
        col = (
            DB.FilteredElementCollector(doc)
            .OfCategory(bic)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        for el in col:
            p = el.LookupParameter(PARAM_NAME)
            if p is None or p.IsReadOnly:
                continue
            if p.HasValue and p.AsString():
                continue
            pid = panel_utils._read_panel_id(el)
            if pid:
                p.Set(pid)
                count += 1
    return count


def main():
    link_zones = _build_zones_from_links()
    host_zones = _build_zones_from_host()
    all_zones = _merge_zones(link_zones, host_zones)

    if not all_zones:
        forms.alert(
            "No panel zones found.\n\n"
            "The tool looked for structural framing/columns with panel IDs "
            "(BIMSF_Container, IfcTag, Mark, etc.) in both the host model "
            "and all linked models — but found nothing.\n\n"
            "Make sure:\n"
            "• The Vertex BD / IFC structural link is loaded\n"
            "• The linked elements have IfcTag or Mark values",
            title="UNIQUBE — Setup BIMSF",
        )
        return

    source = []
    if link_zones:
        source.append("linked models")
    if host_zones:
        source.append("host model")

    panel_count = len(all_zones)
    sample_ids = sorted(all_zones.keys())[:8]

    proceed = forms.alert(
        "Found {} panel zones from {}.\n\n"
        "Sample panel IDs:\n{}\n\n"
        "This will:\n"
        "1. Add BIMSF_Container parameter to MEP categories\n"
        "2. Auto-tag every host MEP element based on which "
        "panel zone it falls in\n\n"
        "No link binding required. Continue?".format(
            panel_count,
            " + ".join(source),
            "\n".join("  • " + s for s in sample_ids),
        ),
        title="UNIQUBE — Setup BIMSF",
        yes=True,
        no=True,
    )

    if not proceed:
        return

    with revit.Transaction("UNIQUBE: Add BIMSF_Container parameter"):
        param_ok = _ensure_bimsf_on_mep(doc)

    if not param_ok:
        return

    with revit.Transaction("UNIQUBE: Auto-tag MEP from panel zones"):
        tagged, multi, untagged = _tag_host_mep(all_zones)
        struct_tagged = _tag_host_struct(all_zones)

    msg = (
        "Setup complete!\n\n"
        "MEP elements tagged: {}\n"
        "  — in multiple panels (crossing): {}\n"
        "  — not in any zone: {}\n".format(tagged, multi, untagged)
    )
    if struct_tagged > 0:
        msg += "Host structural elements tagged: {}\n".format(struct_tagged)
    msg += (
        "\nYou can now use:\n"
        "• Panel Combine (Color) — to visualize panels\n"
        "• Panel Combine (Assembly) — to create assemblies\n"
        "• Group Panels / Ungroup Panels"
    )
    forms.alert(msg, title="UNIQUBE — Setup BIMSF")


main()
