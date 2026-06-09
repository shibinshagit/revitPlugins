# -*- coding: utf-8 -*-
"""Master panel list for factory tracking - as a LIVE Revit schedule.

Builds a native Revit schedule (so it stays live) grouped by panel, showing
each panel's one-step-down components (e.g. CT003-3) with a quantity, plus
length / height / thickness / weight. Panel names are shown in red, the
component rows in black. Also offers a CSV download.

Because a Revit schedule can only display element parameters, the computed
values are written into shared parameters on the framing members, then the
schedule is built from them. Re-run the button to refresh after changes.
"""
import os
from pyrevit import revit, DB, forms, script

doc = revit.doc
app = doc.Application
output = script.get_output()
logger = script.get_logger()

CONTAINER_PARAM = "BIMSF_Container"
STEEL_DENSITY_LB_FT3 = 490.0
SCHEDULE_NAME = "UNIQUBE - Master Panel List"

# Shared parameters this tool manages (all Text for clean grouping).
UQ_PANEL = "UQ_Panel"
UQ_COMPONENT = "UQ_Component"
UQ_LENGTH = "UQ_Length"
UQ_HEIGHT = "UQ_Height"
UQ_THICKNESS = "UQ_Thickness"
UQ_WEIGHT = "UQ_Weight"
UQ_PARAMS = [
    UQ_PANEL, UQ_COMPONENT, UQ_LENGTH, UQ_HEIGHT, UQ_THICKNESS, UQ_WEIGHT,
]


# --------------- element helpers ---------------

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


def _assembly_of(elem):
    try:
        aid = elem.AssemblyInstanceId
    except Exception:
        return None
    if aid is None or aid == DB.ElementId.InvalidElementId:
        return None
    return doc.GetElement(aid)


def _panel_of(elem):
    """Panel number: member's own container, else its assembly's container."""
    p = elem.LookupParameter(CONTAINER_PARAM)
    if p and p.HasValue and p.AsString():
        return p.AsString()
    asm = _assembly_of(elem)
    if asm is not None:
        cp = asm.LookupParameter(CONTAINER_PARAM)
        if cp and cp.HasValue and cp.AsString():
            return cp.AsString()
    return None


def _component_of(elem):
    """One-step-down item: assembly name (e.g. CT003-3), else family type."""
    asm = _assembly_of(elem)
    if asm is not None:
        try:
            if asm.Name:
                return asm.Name
        except Exception:
            pass
    t = doc.GetElement(elem.GetTypeId())
    if t is not None:
        try:
            name = DB.Element.Name.__get__(t)
            if name:
                return name
        except Exception:
            try:
                return t.Name
            except Exception:
                pass
    return "Unknown"


def collect_panels():
    """Return {panel_number: [framing elements]} for walls and floor trusses."""
    panels = {}
    framing = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for el in framing:
        pid = _panel_of(el)
        if not pid:
            continue
        panels.setdefault(pid, []).append(el)
    return panels


def _panel_dims(elements):
    """(length, height, thickness) in feet from combined bounding box."""
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
    dx, dy, dz = (max_x - min_x), (max_y - min_y), (max_z - min_z)
    horiz = sorted([dx, dy])
    return (horiz[1], dz, horiz[0])  # length, height, thickness


def _get_volume_ft3(elem):
    p = elem.get_Parameter(DB.BuiltInParameter.HOST_VOLUME_COMPUTED)
    if p and p.HasValue:
        return p.AsDouble()
    p = elem.LookupParameter("Volume")
    if p and p.HasValue:
        return p.AsDouble()
    return 0.0


def _panel_weight_lb(elements):
    total = 0.0
    found = False
    for el in elements:
        wp = el.LookupParameter("Weight")
        if wp and wp.HasValue and wp.AsDouble() > 0:
            total += wp.AsDouble()
            found = True
    if found and total > 0:
        return total
    return sum(_get_volume_ft3(el) for el in elements) * STEEL_DENSITY_LB_FT3


def _fmt_length(value_ft):
    try:
        return DB.UnitFormatUtils.Format(
            doc.GetUnits(), DB.SpecTypeId.Length, value_ft, False
        )
    except Exception:
        return "{0:.3f} ft".format(value_ft)


# --------------- shared parameters ---------------

def ensure_shared_params():
    """Create + bind the UQ_* text parameters to Structural Framing if missing."""
    bindings = doc.ParameterBindings
    existing = set()
    it = bindings.ForwardIterator()
    it.Reset()
    while it.MoveNext():
        try:
            existing.add(it.Key.Name)
        except Exception:
            pass
    missing = [n for n in UQ_PARAMS if n not in existing]
    if not missing:
        return True

    spath = os.path.join(os.path.dirname(__file__), "uq_shared_params.txt")
    if not os.path.exists(spath):
        with open(spath, "w") as f:
            f.write("")

    old = app.SharedParametersFilename
    app.SharedParametersFilename = spath
    deffile = app.OpenSharedParameterFile()
    if deffile is None:
        app.SharedParametersFilename = old
        return False

    grp = None
    for g in deffile.Groups:
        if g.Name == "UNIQUBE":
            grp = g
            break
    if grp is None:
        grp = deffile.Groups.Create("UNIQUBE")

    catset = app.Create.NewCategorySet()
    cat = doc.Settings.Categories.get_Item(
        DB.BuiltInCategory.OST_StructuralFraming
    )
    catset.Insert(cat)
    binding = app.Create.NewInstanceBinding(catset)

    for name in missing:
        definition = None
        for d in grp.Definitions:
            if d.Name == name:
                definition = d
                break
        if definition is None:
            opt = DB.ExternalDefinitionCreationOptions(
                name, DB.SpecTypeId.String.Text
            )
            definition = grp.Definitions.Create(opt)
        try:
            doc.ParameterBindings.Insert(definition, binding, DB.GroupTypeId.Data)
        except Exception:
            try:
                doc.ParameterBindings.Insert(
                    definition, binding, DB.BuiltInParameterGroup.PG_DATA
                )
            except Exception as ex:
                logger.debug("Bind failed for %s: %s", name, ex)

    app.SharedParametersFilename = old
    return True


def _set(elem, name, value):
    p = elem.LookupParameter(name)
    if p and not p.IsReadOnly:
        try:
            p.Set(value)
        except Exception:
            pass


def populate_params(panels):
    """Write UQ_* values onto every framing member, returns summary rows."""
    summary = []
    for pid in sorted(panels.keys()):
        elements = panels[pid]
        dims = _panel_dims(elements)
        if dims is None:
            length = height = thickness = "-"
        else:
            length = _fmt_length(dims[0])
            height = _fmt_length(dims[1])
            thickness = _fmt_length(dims[2])
        weight = "{0:.1f}".format(_panel_weight_lb(elements))

        comp_counts = {}
        for el in elements:
            comp = _component_of(el)
            comp_counts[comp] = comp_counts.get(comp, 0) + 1
            _set(el, UQ_PANEL, pid)
            _set(el, UQ_COMPONENT, comp)
            _set(el, UQ_LENGTH, length)
            _set(el, UQ_HEIGHT, height)
            _set(el, UQ_THICKNESS, thickness)
            _set(el, UQ_WEIGHT, weight)

        for comp in sorted(comp_counts.keys()):
            summary.append([
                pid, comp, comp_counts[comp],
                length, height, thickness, weight,
            ])
    return summary


# --------------- schedule ---------------

def _add_field(defn, sched_fields, name, hidden=False):
    for sf in sched_fields:
        if sf.GetName(doc) == name:
            field = defn.AddField(sf)
            if hidden:
                field.IsHidden = True
            return field
    return None


def build_schedule():
    for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule):
        if s.Name == SCHEDULE_NAME:
            doc.Delete(s.Id)
            break

    sched = DB.ViewSchedule.CreateSchedule(
        doc, DB.ElementId(DB.BuiltInCategory.OST_StructuralFraming)
    )
    sched.Name = SCHEDULE_NAME
    defn = sched.Definition
    sched_fields = defn.GetSchedulableFields()

    panel_field = _add_field(defn, sched_fields, UQ_PANEL)
    comp_field = _add_field(defn, sched_fields, UQ_COMPONENT)
    count_field = _add_field(defn, sched_fields, "Count")
    _add_field(defn, sched_fields, UQ_LENGTH)
    _add_field(defn, sched_fields, UQ_HEIGHT)
    _add_field(defn, sched_fields, UQ_THICKNESS)
    _add_field(defn, sched_fields, UQ_WEIGHT)

    # Only show rows that this tool tagged
    if panel_field:
        defn.AddFilter(
            DB.ScheduleFilter(
                panel_field.FieldId, DB.ScheduleFilterType.HasValue
            )
        )

    if panel_field:
        sgf = DB.ScheduleSortGroupField(panel_field.FieldId)
        sgf.ShowHeader = True
        defn.AddSortGroupField(sgf)
    if comp_field:
        defn.AddSortGroupField(DB.ScheduleSortGroupField(comp_field.FieldId))

    defn.IsItemized = False
    return sched, panel_field


def color_panel_red(sched):
    """Best-effort: render the panel column / header rows in red."""
    try:
        red = DB.Color(255, 0, 0)
        style = DB.TableCellStyle()
        opts = DB.TableCellStyleOverrideOptions()
        opts.FontColor = True
        style.SetCellStyleOverrideOptions(opts)
        style.TextColor = red

        td = sched.GetTableData()
        body = td.GetSectionData(DB.SectionType.Body)
        n_rows = body.NumberOfRows
        n_cols = body.NumberOfColumns
        for r in range(n_rows):
            # Header rows (grouped panel) have the panel value but a blank
            # Count cell; color those entire rows red.
            panel_text = sched.GetCellText(DB.SectionType.Body, r, 0)
            count_text = ""
            if n_cols > 2:
                count_text = sched.GetCellText(DB.SectionType.Body, r, 2)
            if panel_text and not count_text:
                for c in range(n_cols):
                    body.SetCellStyle(r, c, style)
    except Exception as ex:
        logger.debug("Coloring failed (non-fatal): %s", ex)


def _csv_field(value):
    text = u"{}".format(value)
    return u'"' + text.replace(u'"', u'""') + u'"'


def export_csv(headers, rows):
    save_path = forms.save_file(file_ext="csv", default_name="MasterPanelList")
    if not save_path:
        return None
    import codecs
    with codecs.open(save_path, "w", encoding="utf-8-sig") as f:
        f.write(u",".join(_csv_field(h) for h in headers) + u"\n")
        for row in rows:
            f.write(u",".join(_csv_field(c) for c in row) + u"\n")
    return save_path


# --------------- main ---------------

def main():
    panels = collect_panels()
    if not panels:
        forms.alert(
            "No panels with '{}' found.".format(CONTAINER_PARAM),
            title="UNIQUBE",
        )
        return

    headers = [
        "panel number", "component", "qty",
        "length", "height", "thickness", "weight (lb)",
    ]

    with revit.Transaction("UNIQUBE: Master Panel List"):
        if not ensure_shared_params():
            forms.alert(
                "Could not create the UQ shared parameters.",
                title="UNIQUBE",
            )
            return
        summary = populate_params(panels)
        sched, _panel_field = build_schedule()
        color_panel_red(sched)

    output.print_md("# UNIQUBE - Master Panel List")
    output.print_table(table_data=summary, columns=headers)

    saved = export_csv(headers, summary)

    msg = "Live schedule created: '{}'.\nPanels: {}".format(
        SCHEDULE_NAME, len(panels)
    )
    if saved:
        msg += "\nCSV saved: {}".format(saved)
    forms.alert(msg, title="UNIQUBE - Master Panel List")

    try:
        revit.active_view = sched
    except Exception:
        pass


main()
