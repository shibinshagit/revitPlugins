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
import panel_utils as pu

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


def _container(elem):
    p = elem.LookupParameter(CONTAINER_PARAM)
    if p and p.HasValue:
        return p.AsString() or ""
    return ""


def _in_assembly(elem):
    try:
        aid = elem.AssemblyInstanceId
    except Exception:
        return False
    return aid is not None and aid != DB.ElementId.InvalidElementId


def collect_units():
    """Return {panel_number: [ [members of one instance], ... ]}.

    Quantity for a panel number is the number of instances (assemblies) that
    share it. The panel number is taken verbatim from BIMSF_Container, so the
    asterisk is preserved (e.g. *ELB-1001). Each assembly is one instance;
    loose framing sharing a container counts as one instance.
    """
    units = {}
    assembled = set()

    assemblies = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.AssemblyInstance)
        .ToElements()
    )
    for asm in assemblies:
        members = []
        for mid in asm.GetMemberIds():
            m = doc.GetElement(mid)
            if m is not None and _is_structural_framing(m):
                members.append(m)
        if not members:
            continue
        # Panel number: assembly container, else a member's container.
        pno = _container(asm)
        if not pno:
            for m in members:
                pno = _container(m)
                if pno:
                    break
        if not pno:
            continue
        units.setdefault(pno, []).append(members)
        for m in members:
            assembled.add(m.Id.IntegerValue)

    # Loose framing (not in any assembly) — one instance per panel number.
    loose = {}
    framing = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for el in framing:
        if el.Id.IntegerValue in assembled or _in_assembly(el):
            continue
        pno = _container(el)
        if pno:
            loose.setdefault(pno, []).append(el)
    for pno, members in loose.items():
        units.setdefault(pno, []).append(members)

    return units


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


def populate_params(units):
    """Write UQ_* values onto every framing member; return summary rows.

    One row per panel number. Qty = number of instances (assemblies) that
    share the panel number. Dimensions and weight are for a single instance.
    Panel names are shown without the leading '*' prefix.
    """
    summary = []
    for pno in sorted(units.keys(), key=lambda x: pu.panel_display_name(x).lower()):
        instances = units[pno]
        qty = len(instances)
        one = instances[0]
        length, height, thickness = _dims_strings(one)
        weight = "{0:.1f}".format(_panel_weight_lb(one))
        display = pu.panel_display_name(pno)
        for members in instances:
            _write(members, display, str(qty), length, height, thickness, weight)
        summary.append([display, str(qty), length, height, thickness, weight])
    return summary


# --------------- schedule ---------------

def _add_field(defn, sched_fields, name, heading=None, hidden=False):
    for sf in sched_fields:
        if sf.GetName(doc) == name:
            field = defn.AddField(sf)
            if hidden:
                field.IsHidden = True
            if heading:
                try:
                    field.ColumnHeading = heading
                except Exception:
                    pass
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

    panel_field = _add_field(defn, sched_fields, UQ_PANEL, "Panel")
    _add_field(defn, sched_fields, UQ_QTY, "Qty")
    _add_field(defn, sched_fields, UQ_LENGTH, "Length")
    _add_field(defn, sched_fields, UQ_HEIGHT, "Height")
    _add_field(defn, sched_fields, UQ_THICKNESS, "Thickness")
    _add_field(defn, sched_fields, UQ_WEIGHT, "Weight (lb)")

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
    units = collect_units()
    if not units:
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
        summary = populate_params(units)
        sched, _panel_field = build_schedule()
        color_panel_red(sched)

    MasterPanelWindow(headers, summary, sched).ShowDialog()


main()
