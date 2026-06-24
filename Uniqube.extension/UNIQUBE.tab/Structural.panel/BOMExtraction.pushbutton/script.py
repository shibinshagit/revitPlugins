# -*- coding: utf-8 -*-
"""Master BOM schedules for conduits and pipes using view templates."""
from pyrevit import revit, DB, forms, script

doc = revit.doc
logger = script.get_logger()

CONDUIT_TEMPLATE_NAME = "Conduit length"
PIPE_TEMPLATE_NAME = "Pipe Schedule"


def main():
    # Locate view templates
    all_views = (
        DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()
    )
    c_temp = None
    p_temp = None
    for v in all_views:
        if v.IsTemplate:
            if v.Name == CONDUIT_TEMPLATE_NAME:
                c_temp = v
            elif v.Name == PIPE_TEMPLATE_NAME:
                p_temp = v

    bom_configs = [
        {
            "name": "MASTER BOM - Conduits",
            "cat": DB.BuiltInCategory.OST_Conduit,
            "temp": c_temp,
        },
        {
            "name": "MASTER BOM - Pipes",
            "cat": DB.BuiltInCategory.OST_PipeCurves,
            "temp": p_temp,
        },
    ]

    with revit.Transaction("uniqube: BOM Extraction"):
        extracted = []
        for cfg in bom_configs:
            for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule):
                if s.Name == cfg["name"]:
                    doc.Delete(s.Id)
                    break

            new_v = DB.ViewSchedule.CreateSchedule(
                doc, DB.ElementId(cfg["cat"])
            )
            new_v.Name = cfg["name"]
            defn = new_v.Definition
            fields = defn.GetSchedulableFields()

            for target in ["Size", "Length"]:
                for sf in fields:
                    if sf.GetName(doc) == target:
                        defn.AddField(sf)
                        break

            if cfg["temp"]:
                new_v.ViewTemplateId = cfg["temp"].Id

            extracted.append(cfg["name"])

    missing = []
    if c_temp is None:
        missing.append("'{}' (conduit template)".format(CONDUIT_TEMPLATE_NAME))
    if p_temp is None:
        missing.append("'{}' (pipe template)".format(PIPE_TEMPLATE_NAME))

    msg = "BOM extraction complete.\nCreated: {}".format(
        " & ".join(extracted)
    )
    if missing:
        msg += "\n\nWarning: view template(s) not found: {}".format(
            ", ".join(missing)
        )
    forms.alert(msg, title="uniqube")


main()
