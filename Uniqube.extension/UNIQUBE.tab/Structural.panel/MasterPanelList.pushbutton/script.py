# -*- coding: utf-8 -*-
"""Master panel list for factory tracking - as a LIVE Revit schedule.

Builds a native Revit schedule (so it stays live) with one row per panel
name and a quantity. Wall panels are listed by their BIMSF_Container name
(e.g. ELB-1001); floor trusses are listed by their assembly name (e.g.
CT003-3, TB-1). Quantity is the number of identical panels/trusses of that
name. Length / height / thickness / weight are per unit (per wall panel, or
per single truss); weight is the steel only (cut length x the family's
weight per metre), sheathing boards excluded. The panel name column is
shown in red. CSV download too.

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
SCHEDULE_NAME = "UNIQUBE - Master Panel List"

STEEL_DENSITY_KG_M3 = 7850.0
FT_TO_M = 0.3048
FT3_TO_M3 = FT_TO_M ** 3

# Shared parameters this tool manages. The numbers are numeric parameters
# so their Field Format can be set in the schedule.
UQ_PANEL = "UQ_Panel"
UQ_QTY = "UQ_Qty"
UQ_LENGTH = "UQ_Length"
UQ_HEIGHT = "UQ_Height"
UQ_THICKNESS = "UQ_Thickness"
UQ_WEIGHT = "UQ_Weight"
UQ_PARAMS = [
    UQ_PANEL, UQ_QTY, UQ_LENGTH, UQ_HEIGHT, UQ_THICKNESS, UQ_WEIGHT,
]
UQ_SPECS = {
    UQ_PANEL: DB.SpecTypeId.String.Text,
    UQ_QTY: DB.SpecTypeId.Int.Integer,
    UQ_LENGTH: DB.SpecTypeId.Length,
    UQ_HEIGHT: DB.SpecTypeId.Length,
    UQ_THICKNESS: DB.SpecTypeId.Length,
    UQ_WEIGHT: DB.SpecTypeId.Mass,
}
# Older versions made every UQ_* parameter Text; numeric ones that clash
# by name with those go into this group of the shared parameter file.
NUMERIC_GROUP = "UNIQUBE Numeric"
WEIGHT_HEADING = "Weight"
# Heading older versions set; replaced so it cannot contradict the unit.
OLD_WEIGHT_HEADING = "Weight (lb)"


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
    """(length, height, thickness) in feet — shop-drawing convention."""
    return pu.compute_panel_dimensions(elements)


def _get_volume_ft3(elem):
    p = elem.get_Parameter(DB.BuiltInParameter.HOST_VOLUME_COMPUTED)
    if p and p.HasValue:
        return p.AsDouble()
    p = elem.LookupParameter("Volume")
    if p and p.HasValue:
        return p.AsDouble()
    return 0.0


def _cut_length_m(elem):
    p = elem.get_Parameter(DB.BuiltInParameter.STRUCTURAL_FRAME_CUT_LENGTH)
    if p is None or not p.HasValue or p.AsDouble() <= 0:
        p = elem.LookupParameter("Cut Length")
    if p is not None and p.HasValue and p.AsDouble() > 0:
        return p.AsDouble() * FT_TO_M
    try:
        return elem.Location.Curve.Length * FT_TO_M
    except Exception:
        return 0.0


def _kg_per_m(param):
    """A 'weight per length' (kgf/m) or 'mass per length' (kg/m) value."""
    if param is None or not param.HasValue or param.StorageType != DB.StorageType.Double:
        return 0.0
    value = param.AsDouble()
    if value <= 0:
        return 0.0
    try:
        spec = param.Definition.GetDataType()
    except Exception:
        return 0.0
    for unit in (DB.UnitTypeId.KilogramsForcePerMeter, DB.UnitTypeId.KilogramsPerMeter):
        try:
            if DB.UnitUtils.IsValidUnit(spec, unit):
                return DB.UnitUtils.ConvertFromInternalUnits(value, unit)
        except Exception:
            pass
    return 0.0


def _mm(elem_type, name):
    p = elem_type.LookupParameter(name)
    if p is None or not p.HasValue or p.StorageType != DB.StorageType.Double:
        return 0.0
    return p.AsDouble() * FT_TO_M * 1000.0


def _section_kg_per_m(elem_type):
    """Lipped C from the type's d, bf, tf and Return (centreline estimate)."""
    d, bf, tf = _mm(elem_type, "d"), _mm(elem_type, "bf"), _mm(elem_type, "tf")
    if d <= 0 or bf <= 0 or tf <= 0:
        return 0.0
    lip = _mm(elem_type, "Return")
    area_mm2 = tf * (d + 2.0 * bf + 2.0 * lip)
    return area_mm2 * 1e-6 * STEEL_DENSITY_KG_M3


def _is_sheathing(elem_type):
    try:
        return "sheathing" in (elem_type.FamilyName or "").lower()
    except Exception:
        return False


def _member_weight_kg(elem):
    """Cut length x the family's weight per metre. Steel only: sheathing
    boards (also Structural Framing) weigh nothing here.

    If the type has no weight per metre, it is worked out from the section
    sizes on the type; only when those are missing too is the geometry
    volume used (family solids are often heavier than the real section).
    """
    elem_type = doc.GetElement(elem.GetTypeId())
    if elem_type is not None and _is_sheathing(elem_type):
        return 0.0
    per_m = _kg_per_m(elem.LookupParameter("Weight"))
    if per_m <= 0 and elem_type is not None:
        per_m = _kg_per_m(elem_type.LookupParameter("Weight"))
        if per_m <= 0:
            per_m = _section_kg_per_m(elem_type)
    if per_m > 0:
        return per_m * _cut_length_m(elem)
    return _get_volume_ft3(elem) * FT3_TO_M3 * STEEL_DENSITY_KG_M3


def _panel_weight_kg(elements):
    return sum(_member_weight_kg(el) for el in elements)


def _fmt_mass(value_kg):
    try:
        return DB.UnitFormatUtils.Format(
            doc.GetUnits(), DB.SpecTypeId.Mass, value_kg, False
        )
    except Exception:
        return "{0:.1f} kg".format(value_kg)


def _fmt_length(value_ft):
    try:
        return DB.UnitFormatUtils.Format(
            doc.GetUnits(), DB.SpecTypeId.Length, value_ft, False
        )
    except Exception:
        return "{0:.3f} ft".format(value_ft)


# --------------- shared parameters ---------------

def _data_type(definition):
    try:
        return definition.GetDataType()
    except Exception:
        return None


def _bound_definitions():
    found = {}
    it = doc.ParameterBindings.ForwardIterator()
    it.Reset()
    while it.MoveNext():
        try:
            found[it.Key.Name] = it.Key
        except Exception:
            pass
    return found


def _drop_wrong_types(bound):
    """Delete UQ_* parameters left as Text by older versions of this tool.

    Their values are rewritten straight after, and the schedule is rebuilt.
    """
    wrong = [
        name for name in UQ_PARAMS
        if name in bound and _data_type(bound[name]) != UQ_SPECS[name]
    ]
    if not wrong:
        return []
    # Collected first: Revit stops a collector if elements are deleted mid-loop.
    old = [
        (spe.Id, spe.Name)
        for spe in DB.FilteredElementCollector(doc).OfClass(DB.SharedParameterElement)
        if spe.Name in wrong
    ]
    for spe_id, name in old:
        try:
            doc.Delete(spe_id)
        except Exception as ex:
            logger.debug("Could not remove old %s: %s", name, ex)
    for name in wrong:
        if name in _bound_definitions():
            try:
                doc.ParameterBindings.Remove(bound[name])
            except Exception as ex:
                logger.debug("Could not unbind old %s: %s", name, ex)
    return wrong


def _file_definition(deffile, name):
    """A definition of the right type from the shared parameter file,
    created if needed."""
    spec = UQ_SPECS[name]
    for group in deffile.Groups:
        for d in group.Definitions:
            if d.Name == name and _data_type(d) == spec:
                return d
    target = None
    for group_name in ("UNIQUBE", NUMERIC_GROUP):
        group = None
        for g in deffile.Groups:
            if g.Name == group_name:
                group = g
                break
        if group is None:
            group = deffile.Groups.Create(group_name)
        if not any(d.Name == name for d in group.Definitions):
            target = group
            break
    if target is None:
        return None
    return target.Definitions.Create(DB.ExternalDefinitionCreationOptions(name, spec))


def ensure_shared_params():
    """Create + bind the UQ_* parameters to Structural Framing if missing."""
    _drop_wrong_types(_bound_definitions())
    existing = set(_bound_definitions())
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

    catset = app.Create.NewCategorySet()
    cat = doc.Settings.Categories.get_Item(
        DB.BuiltInCategory.OST_StructuralFraming
    )
    catset.Insert(cat)
    binding = app.Create.NewInstanceBinding(catset)

    for name in missing:
        definition = _file_definition(deffile, name)
        if definition is None:
            logger.debug("No definition for %s", name)
            continue
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


def _write(members, name, qty, dims, weight_kg):
    """Numbers go in Revit's internal units (feet, kilograms)."""
    for el in members:
        _set(el, UQ_PANEL, name)
        _set(el, UQ_QTY, int(qty))
        _set(el, UQ_LENGTH, float(dims[0]))
        _set(el, UQ_HEIGHT, float(dims[1]))
        _set(el, UQ_THICKNESS, float(dims[2]))
        _set(el, UQ_WEIGHT, float(weight_kg))


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
        dims = _panel_dims(one)
        weight_kg = _panel_weight_kg(one)
        display = pu.panel_display_name(pno)
        for members in instances:
            _write(members, display, qty, dims or (0.0, 0.0, 0.0), weight_kg)
        if dims is None:
            length = height = thickness = "-"
        else:
            length, height, thickness = [_fmt_length(v) for v in dims]
        summary.append([display, str(qty), length, height, thickness,
                        _fmt_mass(weight_kg)])
    return summary


# --------------- schedule ---------------

def _add_field(defn, sched_fields, name, heading=None, hidden=False,
               kept=None, default_format=None):
    """Add a field. ``kept`` holds the heading and Field Format the user
    gave it before this rebuild, which win over the defaults."""
    for sf in sched_fields:
        if sf.GetName(doc) == name:
            field = defn.AddField(sf)
            if hidden:
                field.IsHidden = True
            previous = (kept or {}).get(name)
            if previous is not None:
                heading = previous[0] or heading
            if heading:
                try:
                    field.ColumnHeading = heading
                except Exception:
                    pass
            fmt = previous[1] if previous is not None else default_format
            if fmt is not None:
                try:
                    field.SetFormatOptions(fmt)
                except Exception as ex:
                    logger.debug("Format not applied to %s: %s", name, ex)
            return field
    return None


def _with_symbol(fmt):
    """The unit symbol is written in each cell, so the column follows
    whatever unit is picked in Field Format; Revit never rewrites a
    heading when the unit changes."""
    try:
        if fmt.UseDefault:
            fmt = DB.FormatOptions(doc.GetUnits().GetFormatOptions(DB.SpecTypeId.Mass))
        else:
            fmt = DB.FormatOptions(fmt)
        fmt.UseDefault = False
        if fmt.GetSymbolTypeId().Empty():
            for symbol in DB.FormatOptions.GetValidSymbols(fmt.GetUnitTypeId()):
                if not symbol.Empty():
                    fmt.SetSymbolTypeId(symbol)
                    break
        return fmt
    except Exception as ex:
        logger.debug("Unit symbol not set: %s", ex)
        return None


def _weight_format(kept):
    """Kept user format with a unit symbol, else the project's mass units."""
    previous = kept.get(UQ_WEIGHT)
    if previous is not None and previous[0] == OLD_WEIGHT_HEADING:
        kept[UQ_WEIGHT] = (WEIGHT_HEADING, _with_symbol(previous[1]) or previous[1])
        return None
    return _with_symbol(DB.FormatOptions())


def _keep_field_settings(sched):
    """{parameter name: (heading, FormatOptions)} of the current fields."""
    kept = {}
    defn = sched.Definition
    try:
        for fid in defn.GetFieldOrder():
            field = defn.GetField(fid)
            try:
                kept[field.GetName()] = (field.ColumnHeading, field.GetFormatOptions())
            except Exception:
                pass
    except Exception:
        pass
    return kept


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

    kept = {}
    if sched is None:
        sched = DB.ViewSchedule.CreateSchedule(
            doc, DB.ElementId(DB.BuiltInCategory.OST_StructuralFraming)
        )
        sched.Name = SCHEDULE_NAME
    else:
        kept = _keep_field_settings(sched)
        _clear_schedule(sched)

    defn = sched.Definition
    sched_fields = defn.GetSchedulableFields()

    panel_field = _add_field(defn, sched_fields, UQ_PANEL, "Panel", kept=kept)
    _add_field(defn, sched_fields, UQ_QTY, "Qty", kept=kept)
    _add_field(defn, sched_fields, UQ_LENGTH, "Length", kept=kept)
    _add_field(defn, sched_fields, UQ_HEIGHT, "Height", kept=kept)
    _add_field(defn, sched_fields, UQ_THICKNESS, "Thickness", kept=kept)
    _add_field(defn, sched_fields, UQ_WEIGHT, WEIGHT_HEADING, kept=kept,
               default_format=_weight_format(kept))

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
        "length", "height", "thickness", "weight",
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
