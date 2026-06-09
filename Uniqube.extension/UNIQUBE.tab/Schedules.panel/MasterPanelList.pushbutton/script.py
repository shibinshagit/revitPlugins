# -*- coding: utf-8 -*-
"""Master panel list for factory tracking.

Lists every panel (wall panels and floor trusses) with:
- panel number  (Revit: BIMSF_Container)
- length        (computed: larger horizontal bounding-box dimension)
- height        (computed: vertical bounding-box dimension)
- thickness     (computed: smaller horizontal bounding-box dimension)
- weight        (Revit: sum of member Weight; falls back to volume x steel density)

Shows the table in the pyRevit output window and lets you download it as CSV.
"""
from pyrevit import revit, DB, forms, script

doc = revit.doc
output = script.get_output()
logger = script.get_logger()

CONTAINER_PARAM = "BIMSF_Container"
STEEL_DENSITY_LB_FT3 = 490.0  # approx for steel, used only as a fallback


def _get_container(elem):
    p = elem.LookupParameter(CONTAINER_PARAM)
    if p and p.HasValue:
        return p.AsString()
    return None


def _is_structural_framing(elem):
    cat = elem.Category
    if cat is None:
        return False
    try:
        return cat.BuiltInCategory == DB.BuiltInCategory.OST_StructuralFraming
    except Exception:
        try:
            return cat.Id.IntegerValue == int(
                DB.BuiltInCategory.OST_StructuralFraming
            )
        except Exception:
            return False


def collect_panels(doc):
    """Return {panel_number: [framing elements]} for walls and floor trusses."""
    panels = {}

    # 1. Framing members that carry BIMSF_Container directly (wall studs).
    framing = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for el in framing:
        pid = _get_container(el)
        if not pid:
            continue
        panels.setdefault(pid, []).append(el)

    # 2. Assemblies that carry BIMSF_Container (floor trusses): pull their
    #    members in under the assembly's container (the floor panel).
    assemblies = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.AssemblyInstance)
        .ToElements()
    )
    for asm in assemblies:
        cp = asm.LookupParameter(CONTAINER_PARAM)
        if not (cp and cp.HasValue and cp.AsString()):
            continue
        pid = cp.AsString()
        bucket = panels.setdefault(pid, [])
        bucket_ids = set(e.Id for e in bucket)
        for mid in asm.GetMemberIds():
            m = doc.GetElement(mid)
            if m is None or not _is_structural_framing(m):
                continue
            if m.Id in bucket_ids:
                continue
            bucket.append(m)
            bucket_ids.add(m.Id)

    return panels


def _panel_bbox(elements):
    """Combined bounding box (min, max) for a panel's elements."""
    min_x = min_y = min_z = None
    max_x = max_y = max_z = None
    for el in elements:
        bb = el.get_BoundingBox(None)
        if bb is None:
            continue
        if min_x is None:
            min_x, min_y, min_z = bb.Min.X, bb.Min.Y, bb.Min.Z
            max_x, max_y, max_z = bb.Max.X, bb.Max.Y, bb.Max.Z
        else:
            min_x = min(min_x, bb.Min.X)
            min_y = min(min_y, bb.Min.Y)
            min_z = min(min_z, bb.Min.Z)
            max_x = max(max_x, bb.Max.X)
            max_y = max(max_y, bb.Max.Y)
            max_z = max(max_z, bb.Max.Z)
    if min_x is None:
        return None
    return (max_x - min_x, max_y - min_y, max_z - min_z)


def _get_volume_ft3(elem):
    p = elem.get_Parameter(DB.BuiltInParameter.HOST_VOLUME_COMPUTED)
    if p and p.HasValue:
        return p.AsDouble()
    p = elem.LookupParameter("Volume")
    if p and p.HasValue:
        return p.AsDouble()
    return 0.0


def _get_weight_lb(elements):
    """Sum the members' Weight parameter; fall back to volume x density."""
    total = 0.0
    found_param = False
    for el in elements:
        wp = el.LookupParameter("Weight")
        if wp and wp.HasValue:
            val = wp.AsDouble()
            if val > 0:
                total += val
                found_param = True
    if found_param and total > 0:
        return total
    # Fallback: estimate from volume
    vol = sum(_get_volume_ft3(el) for el in elements)
    return vol * STEEL_DENSITY_LB_FT3


def _fmt_length(value_ft):
    """Format an internal (feet) length using the project's display units."""
    try:
        return DB.UnitFormatUtils.Format(
            doc.GetUnits(), DB.SpecTypeId.Length, value_ft, False
        )
    except Exception:
        return "{0:.3f} ft".format(value_ft)


def _csv_field(value):
    text = u"{}".format(value)
    return u'"' + text.replace(u'"', u'""') + u'"'


def main():
    panels = collect_panels(doc)
    if not panels:
        forms.alert(
            "No panels with '{}' found.".format(CONTAINER_PARAM),
            title="UNIQUBE",
        )
        return

    headers = ["panel number", "length", "height", "thickness", "weight (lb)"]
    rows = []
    for pid in sorted(panels.keys()):
        dims = _panel_bbox(panels[pid])
        if dims is None:
            rows.append([pid, "-", "-", "-", "-"])
            continue
        dx, dy, dz = dims
        horiz = sorted([dx, dy])
        thickness = horiz[0]
        length = horiz[1]
        height = dz
        weight = _get_weight_lb(panels[pid])
        rows.append([
            pid,
            _fmt_length(length),
            _fmt_length(height),
            _fmt_length(thickness),
            "{0:.1f}".format(weight),
        ])

    # Show in the pyRevit output window
    output.print_md("# UNIQUBE - Master Panel List")
    output.print_table(table_data=rows, columns=headers)

    # Offer CSV download
    save_path = forms.save_file(file_ext="csv", default_name="MasterPanelList")
    if not save_path:
        return

    try:
        import codecs
        with codecs.open(save_path, "w", encoding="utf-8-sig") as f:
            f.write(u",".join(_csv_field(h) for h in headers) + u"\n")
            for row in rows:
                f.write(u",".join(_csv_field(c) for c in row) + u"\n")
    except Exception as ex:
        forms.alert("Could not write CSV:\n{}".format(ex), title="UNIQUBE")
        return

    forms.alert(
        "Master panel list saved.\n\nPanels: {}\nFile: {}".format(
            len(rows), save_path
        ),
        title="UNIQUBE - Master Panel List",
    )


main()
