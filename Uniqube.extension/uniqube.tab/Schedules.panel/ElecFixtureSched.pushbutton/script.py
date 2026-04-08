# -*- coding: utf-8 -*-
"""Per-panel electrical fixture schedules filtered by BIMSF_Container."""
from pyrevit import revit, DB, forms, script

doc = revit.doc
logger = script.get_logger()

PARAM_NAME = "BIMSF_Container"


def main():
    fixtures = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_ElectricalFixtures)
        .WhereElementIsNotElementType()
        .ToElements()
    )

    unique_ids = set()
    for f in fixtures:
        param = f.LookupParameter(PARAM_NAME)
        if param and param.HasValue and param.AsString():
            unique_ids.add(param.AsString())

    if not unique_ids:
        forms.alert(
            "No electrical fixtures with '{}' found.".format(PARAM_NAME),
            title="uniqube",
        )
        return

    with revit.Transaction("uniqube: Electrical Fixture Schedule"):
        created = 0
        for pid in unique_ids:
            sched_name = "{} Electrical Fixture".format(pid)

            for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule):
                if s.Name == sched_name:
                    doc.Delete(s.Id)
                    break

            new_sched = DB.ViewSchedule.CreateSchedule(
                doc, DB.ElementId(DB.BuiltInCategory.OST_ElectricalFixtures)
            )
            new_sched.Name = sched_name
            defn = new_sched.Definition
            sched_fields = defn.GetSchedulableFields()

            target_names = ["Type", "Count"]
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
                defn.AddSortGroupField(
                    DB.ScheduleSortGroupField(added["Type"])
                )

            defn.IsItemized = False
            defn.ShowGrandTotal = True
            created += 1

    forms.alert(
        "{} electrical fixture schedule(s) created.".format(created),
        title="uniqube",
    )


main()
