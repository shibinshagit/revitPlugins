# -*- coding: utf-8 -*-
"""Per-panel conduit fitting schedules (excludes Standard and Bends)."""
from pyrevit import revit, DB, forms, script

doc = revit.doc
logger = script.get_logger()

PARAM_NAME = "BIMSF_Container"
FILTER_PROP = "Bend or Fitting"


def main():
    all_fittings = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_ConduitFitting)
        .WhereElementIsNotElementType()
        .ToElements()
    )

    with revit.Transaction("uniqube: Conduit Fitting Schedule"):
        # Pre-cleanup: clear BIMSF_Container for bends
        for f in all_fittings:
            bend_param = f.LookupParameter(FILTER_PROP)
            if bend_param and bend_param.HasValue:
                val = bend_param.AsString()
                if val and "bend" in val.lower():
                    container_param = f.LookupParameter(PARAM_NAME)
                    if container_param and not container_param.IsReadOnly:
                        container_param.Set("")

        # Collect unique panel IDs
        unique_ids = set()
        for f in all_fittings:
            p = f.LookupParameter(PARAM_NAME)
            if p and p.HasValue and p.AsString():
                unique_ids.add(p.AsString())

        if not unique_ids:
            forms.alert(
                "No conduit fittings with '{}' found.".format(PARAM_NAME),
                title="uniqube",
            )
            return

        created = 0
        for pid in unique_ids:
            sched_name = "Panel_{}__Conduit_Fitting_Schedule".format(pid)

            # Delete existing schedule with same name
            for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule):
                if s.Name == sched_name:
                    doc.Delete(s.Id)
                    break

            new_sched = DB.ViewSchedule.CreateSchedule(
                doc, DB.ElementId(DB.BuiltInCategory.OST_ConduitFitting)
            )
            new_sched.Name = sched_name
            defn = new_sched.Definition
            sched_fields = defn.GetSchedulableFields()

            target_names = ["Type", "Size", "Count"]
            added = {}
            for t_name in target_names:
                for sf in sched_fields:
                    if sf.GetName(doc) == t_name and t_name not in added:
                        field = defn.AddField(sf)
                        added[t_name] = field.FieldId
                        break

            container_fid = None
            for sf in sched_fields:
                if sf.GetName(doc) == PARAM_NAME:
                    field = defn.AddField(sf)
                    container_fid = field.FieldId
                    field.IsHidden = True
                    break

            if container_fid:
                defn.AddFilter(
                    DB.ScheduleFilter(
                        container_fid, DB.ScheduleFilterType.Equal, pid
                    )
                )

            if "Type" in added:
                defn.AddFilter(
                    DB.ScheduleFilter(
                        added["Type"],
                        DB.ScheduleFilterType.NotContains,
                        "Standard",
                    )
                )
                defn.AddFilter(
                    DB.ScheduleFilter(
                        added["Type"],
                        DB.ScheduleFilterType.NotContains,
                        "Primary",
                    )
                )

            for key in ["Type", "Size"]:
                if key in added:
                    defn.AddSortGroupField(
                        DB.ScheduleSortGroupField(added[key])
                    )

            defn.IsItemized = False
            defn.ShowGrandTotal = True
            created += 1

    forms.alert(
        "{} conduit fitting schedule(s) created.\n"
        "'Standard' and 'Bend' items excluded.".format(created),
        title="uniqube",
    )


main()
