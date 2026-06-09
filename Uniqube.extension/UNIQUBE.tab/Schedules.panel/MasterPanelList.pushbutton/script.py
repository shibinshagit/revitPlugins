# -*- coding: utf-8 -*-
"""Master panel list for factory tracking - as a LIVE Revit schedule.

Builds a native Revit schedule (so it stays live) with one row per panel
name and a quantity. Wall panels are listed by their BIMSF_Container name
(e.g. ELB-1001); floor trusses are listed by their assembly name (e.g.
CT003-3, TB-1). Quantity is the number of identical panels/trusses of that
name. Length / height / thickness / weight are per unit (per wall panel, or
per single truss). The panel name column is shown in red. CSV download too.

Because a Revit schedule can only display element parameters, the computed
values are written into shared parameters on the framing members, then the
schedule is built from them. Re-run the button to refresh after changes.
"""
import os
import clr
from pyrevit import revit, DB, forms, script

clr.AddReference("System.Data")
from System.Data import DataTable
from System import Array, Object

doc = revit.doc
app = doc.Application
logger = script.get_logger()

CONTAINER_PARAM = "BIMSF_Container"
STEEL_DENSITY_LB_FT3 = 490.0
SCHEDULE_NAME = "UNIQUBE - Master Panel List"

# Shared parameters this tool manages (all Text for clean grouping).
UQ_PANEL = "UQ_Panel"
UQ_QTY = "UQ_Qty"
UQ_LENGTH = "UQ_Length"
UQ_HEIGHT = "UQ_Height"
UQ_THICKNESS = "UQ_Thickness"
UQ_WEIGHT = "UQ_Weight"
UQ_PARAMS = [
    UQ_PANEL, UQ_QTY, UQ_LENGTH, UQ_HEIGHT, UQ_THICKNESS, UQ_WEIGHT,
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


def _display_name(raw):
    """Strip the leading '*' from a panel name (e.g. *ELB-1001 -> ELB-1001)."""
    if raw is None:
        return ""
    return raw.lstrip("*")


def collect_units():
    """Collect leaf units to list.

    Returns:
      wall_panels: {container_name: [members]} - framing with a direct
                   BIMSF_Container (one wall panel each).
      truss_groups: {assembly_name: [ [members of instance], ... ]} - one
                    list of member-lists per assembly name (floor trusses).
    """
    wall_panels = {}
    framing = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for el in framing:
        p = el.LookupParameter(CONTAINER_PARAM)
        if p and p.HasValue and p.AsString():
            wall_panels.setdefault(p.AsString(), []).append(el)

    truss_groups = {}
    assemblies = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.AssemblyInstance)
        .ToElements()
    )
    for asm in assemblies:
        cp = asm.LookupParameter(CONTAINER_PARAM)
        if not (cp and cp.HasValue and cp.AsString()):
            continue
        members = []
        for mid in asm.GetMemberIds():
            m = doc.GetElement(mid)
            if m is None or not _is_structural_framing(m):
                continue
            mc = m.LookupParameter(CONTAINER_PARAM)
            if mc and mc.HasValue and mc.AsString():
                continue  # belongs to a wall panel, already counted
            members.append(m)
        if not members:
            continue
        try:
            name = asm.Name
        except Exception:
            name = None
        if not name:
            name = cp.AsString()
        truss_groups.setdefault(name, []).append(members)

    return wall_panels, truss_groups


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


def _dims_strings(members):
    dims = _panel_dims(members)
    if dims is None:
        return ("-", "-", "-")
    return (_fmt_length(dims[0]), _fmt_length(dims[1]), _fmt_length(dims[2]))


def _write(members, name, qty, length, height, thickness, weight):
    for el in members:
        _set(el, UQ_PANEL, name)
        _set(el, UQ_QTY, qty)
        _set(el, UQ_LENGTH, length)
        _set(el, UQ_HEIGHT, height)
        _set(el, UQ_THICKNESS, thickness)
        _set(el, UQ_WEIGHT, weight)


def populate_params(wall_panels, truss_groups):
    """Write UQ_* values onto every framing member; return summary rows.

    One row per panel name: wall panels (qty 1, panel dims) and floor
    trusses (qty = instance count, dims of a single truss).
    """
    summary = []

    # Wall panels: one row each, qty 1.
    for container in sorted(wall_panels.keys()):
        members = wall_panels[container]
        length, height, thickness = _dims_strings(members)
        weight = "{0:.1f}".format(_panel_weight_lb(members))
        name = _display_name(container)
        _write(members, name, "1", length, height, thickness, weight)
        summary.append([name, "1", length, height, thickness, weight])

    # Floor trusses: one row per assembly name, qty = number of instances,
    # dims/weight from a single instance.
    for raw_name in sorted(truss_groups.keys()):
        instances = truss_groups[raw_name]
        qty = len(instances)
        one = instances[0]
        length, height, thickness = _dims_strings(one)
        weight = "{0:.1f}".format(_panel_weight_lb(one))
        name = _display_name(raw_name)
        for members in instances:
            _write(members, name, str(qty), length, height, thickness, weight)
        summary.append([name, str(qty), length, height, thickness, weight])

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


def _clear_schedule(sched):
    """Strip a schedule's fields, sorting, and filters so it can be rebuilt.

    Reusing the schedule (instead of delete + recreate) avoids the
    'ElementId cannot be deleted' error when it is the active view.
    """
    defn = sched.Definition
    try:
        defn.ClearFilters()
    except Exception:
        pass
    try:
        for i in range(defn.GetSortGroupFieldCount() - 1, -1, -1):
            defn.RemoveSortGroupField(i)
    except Exception:
        pass
    try:
        for fid in list(defn.GetFieldOrder()):
            try:
                defn.RemoveField(fid)
            except Exception:
                pass
    except Exception:
        pass


def build_schedule():
    sched = None
    for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule):
        if s.Name == SCHEDULE_NAME:
            sched = s
            break

    if sched is None:
        sched = DB.ViewSchedule.CreateSchedule(
            doc, DB.ElementId(DB.BuiltInCategory.OST_StructuralFraming)
        )
        sched.Name = SCHEDULE_NAME
    else:
        _clear_schedule(sched)

    defn = sched.Definition
    sched_fields = defn.GetSchedulableFields()

    panel_field = _add_field(defn, sched_fields, UQ_PANEL)
    _add_field(defn, sched_fields, UQ_QTY)
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
        # Sort by panel name; not itemized collapses each name to one row.
        defn.AddSortGroupField(DB.ScheduleSortGroupField(panel_field.FieldId))

    defn.IsItemized = False
    return sched, panel_field


def color_panel_red(sched):
    """Render the panel-name column (column 0) in red for every row."""
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
        for r in range(n_rows):
            body.SetCellStyle(r, 0, style)
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


# --------------- viewer window ---------------

class MasterPanelWindow(forms.WPFWindow):
    """Shows the panel list. Download and navigation only fire on their buttons."""

    def __init__(self, headers, rows, sched):
        forms.WPFWindow.__init__(self, "MasterPanelView.xaml")
        self._headers = headers
        self._rows = rows
        self._sched = sched

        table = DataTable()
        for h in headers:
            table.Columns.Add(h)
        for r in rows:
            arr = Array[Object]([u"{}".format(c) for c in r])
            table.Rows.Add(arr)
        self.grid.ItemsSource = table.DefaultView

    def download_click(self, sender, args):
        try:
            saved = export_csv(self._headers, self._rows)
            if saved:
                forms.alert("CSV saved:\n{}".format(saved), title="UNIQUBE")
        except Exception as ex:
            forms.alert("Could not write CSV:\n{}".format(ex), title="UNIQUBE")

    def goto_click(self, sender, args):
        self.Close()
        try:
            revit.active_view = self._sched
        except Exception as ex:
            logger.debug("Go to schedule failed: %s", ex)

    def close_click(self, sender, args):
        self.Close()


# --------------- main ---------------

def main():
    wall_panels, truss_groups = collect_units()
    if not wall_panels and not truss_groups:
        forms.alert(
            "No panels with '{}' found.".format(CONTAINER_PARAM),
            title="UNIQUBE",
        )
        return

    headers = [
        "panel name", "qty",
        "length", "height", "thickness", "weight (lb)",
    ]

    with revit.Transaction("UNIQUBE: Master Panel List"):
        if not ensure_shared_params():
            forms.alert(
                "Could not create the UQ shared parameters.",
                title="UNIQUBE",
            )
            return
        summary = populate_params(wall_panels, truss_groups)
        sched, _panel_field = build_schedule()
        color_panel_red(sched)

    MasterPanelWindow(headers, summary, sched).ShowDialog()


main()
