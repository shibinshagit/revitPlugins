# -*- coding: utf-8 -*-
"""Overall master BOM schedules for the full project.

Creates project-wide schedules for:
- Pipes (Type, Size, Length)
- Conduits (Type, Size, Length)
- Electrical fixtures (Type, Count)
- Pipe fittings (Type, Size, Count)
- Conduit fittings (Type, Size, Count)
"""
from pyrevit import revit, DB, forms, script

doc = revit.doc
logger = script.get_logger()

CONDUIT_TEMPLATE_NAME = "Conduit length"
PIPE_TEMPLATE_NAME = "Pipe Schedule"

TYPE_EXCLUDES = ("Standard", "Primary")
CONDUIT_FITTING_TYPE_EXCLUDES = TYPE_EXCLUDES + ("Bend",)


def _delete_schedule(name):
    for sched in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule):
        if sched.Name == name:
            doc.Delete(sched.Id)
            return


def _find_view_template(name):
    for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
        if view.IsTemplate and view.Name == name:
            return view
    return None


def _add_fields(defn, field_names):
    added = {}
    sched_fields = defn.GetSchedulableFields()
    for target in field_names:
        for sf in sched_fields:
            if sf.GetName(doc) == target and target not in added:
                field = defn.AddField(sf)
                added[target] = field.FieldId
                break
    return added


def _add_type_filters(defn, type_fid, excludes):
    for text in excludes:
        defn.AddFilter(
            DB.ScheduleFilter(
                type_fid, DB.ScheduleFilterType.NotContains, text
            )
        )


def _add_sort_groups(defn, added, keys):
    for key in keys:
        if key in added:
            defn.AddSortGroupField(
                DB.ScheduleSortGroupField(added[key])
            )


def _create_count_schedule(name, category, field_names, sort_keys, type_excludes=None):
    _delete_schedule(name)
    sched = DB.ViewSchedule.CreateSchedule(doc, DB.ElementId(category))
    sched.Name = name
    defn = sched.Definition
    added = _add_fields(defn, field_names)

    if type_excludes and "Type" in added:
        _add_type_filters(defn, added["Type"], type_excludes)

    _add_sort_groups(defn, added, sort_keys)
    defn.IsItemized = False
    defn.ShowGrandTotal = True
    return sched


def _create_length_schedule(name, category, view_template=None):
    _delete_schedule(name)
    sched = DB.ViewSchedule.CreateSchedule(doc, DB.ElementId(category))
    sched.Name = name
    defn = sched.Definition
    added = _add_fields(defn, ["Type", "Size", "Length"])
    _add_sort_groups(defn, added, ["Type", "Size"])
    defn.IsItemized = False
    defn.ShowGrandTotal = True

    if view_template:
        sched.ViewTemplateId = view_template.Id

    return sched


def main():
    conduit_template = _find_view_template(CONDUIT_TEMPLATE_NAME)
    pipe_template = _find_view_template(PIPE_TEMPLATE_NAME)

    with revit.Transaction("uniqube: Overall BOM Extraction"):
        created = []

        _create_length_schedule(
            "MASTER BOM - Pipes",
            DB.BuiltInCategory.OST_PipeCurves,
            pipe_template,
        )
        created.append("MASTER BOM - Pipes")

        _create_length_schedule(
            "MASTER BOM - Conduits",
            DB.BuiltInCategory.OST_Conduit,
            conduit_template,
        )
        created.append("MASTER BOM - Conduits")

        _create_count_schedule(
            "MASTER BOM - Electrical Fixtures",
            DB.BuiltInCategory.OST_ElectricalFixtures,
            ["Type", "Count"],
            ["Type"],
            TYPE_EXCLUDES,
        )
        created.append("MASTER BOM - Electrical Fixtures")

        _create_count_schedule(
            "MASTER BOM - Pipe Fittings",
            DB.BuiltInCategory.OST_PipeFitting,
            ["Type", "Size", "Count"],
            ["Type", "Size"],
            TYPE_EXCLUDES,
        )
        created.append("MASTER BOM - Pipe Fittings")

        _create_count_schedule(
            "MASTER BOM - Conduit Fittings",
            DB.BuiltInCategory.OST_ConduitFitting,
            ["Type", "Size", "Count"],
            ["Type", "Size"],
            CONDUIT_FITTING_TYPE_EXCLUDES,
        )
        created.append("MASTER BOM - Conduit Fittings")

    missing = []
    if conduit_template is None:
        missing.append("'{}' (conduit template)".format(CONDUIT_TEMPLATE_NAME))
    if pipe_template is None:
        missing.append("'{}' (pipe template)".format(PIPE_TEMPLATE_NAME))

    msg = (
        "Overall BOM extraction complete.\n\n"
        "Created {} master schedule(s):\n- {}".format(
            len(created), "\n- ".join(created)
        )
    )
    if missing:
        msg += (
            "\n\nWarning: view template(s) not found: {}. "
            "Pipe and conduit schedules were still created without templates."
        ).format(", ".join(missing))

    forms.alert(msg, title="uniqube")


main()
