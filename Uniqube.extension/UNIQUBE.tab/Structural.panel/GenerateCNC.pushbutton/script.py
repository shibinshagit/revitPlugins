# -*- coding: utf-8 -*-
"""Generate CFS CNC CSV files for rollformer export (Vertex BD format).

Exports wall panels (BIMSF_Container groups) and floor-truss assemblies.
Writes one CSV per unit with COMPONENT rows and DIMPLE / SWAGE /
LIP NOTCH / WEB NOTCH operations derived from member crossings.

One window does the whole job: the panels and trusses on the left, the
program drawn in the centre, and its CNC values in tables on the right, so
each program can be checked before it is written.

The window stays open beside Revit. This script only opens it; from then on
uniqube_cnc_session keeps it alive, has Revit do its model work, and tells
it when the model changes. Pressing the button again brings it forward.
"""
from __future__ import print_function

import os

from pyrevit import EXEC_PARAMS, HOST_APP, revit, forms, script

import uniqube_cnc_export as cnc
import uniqube_cnc_session as cnc_session

doc = revit.doc
logger = script.get_logger()


def _project_name():
    try:
        pi = doc.ProjectInformation
        p = pi.LookupParameter("Project Name")
        if p and p.HasValue:
            name = (p.AsString() or "").strip()
            if name and name.lower() != "project name":
                return name
    except Exception:
        pass
    return "MULK Test"


def _default_out_dir():
    """<model folder>\\CNC, created on demand; Desktop if the model is unsaved."""
    base = ""
    try:
        if doc.PathName:
            model_dir = os.path.dirname(doc.PathName)
            # Don't nest a CNC folder inside a folder already called CNC.
            if os.path.basename(model_dir).upper() == "CNC":
                base = model_dir
            else:
                base = os.path.join(model_dir, "CNC")
    except Exception:
        pass
    if not base:
        base = os.path.join(
            os.path.expanduser("~"), "Desktop", "CNC"
        )
    if not os.path.isdir(base):
        try:
            os.makedirs(base)
        except Exception:
            return os.path.dirname(base) or os.path.expanduser("~")
    return base


def _preselected_names():
    """Panel / truss names for whatever is selected in Revit right now."""
    try:
        elements = [doc.GetElement(eid) for eid in revit.get_selection().element_ids]
    except Exception:
        return set()
    return cnc.unit_names_for_elements(doc, elements)


def _stay_open():
    """True when the window can be left open beside Revit.

    That needs the engine running this script to stay alive afterwards, which
    bundle.yaml asks pyRevit for. pyRevit reads that setting when it loads, so
    a pyRevit that has not been reloaded since says the engine is not kept,
    and only then is the window shown as a dialog instead.
    """
    return cnc_session.engine_outlives_script(EXEC_PARAMS) is not False


def main():
    if cnc_session.reuse_session(doc):
        return  # the window is already open on this model

    with forms.ProgressBar(title="Scanning framing...", indeterminate=True):
        units = cnc.collect_cnc_units(doc)

    if not units:
        forms.alert(
            "No CNC units found.\n\n"
            "Each panel needs framing with BIMSF_Container plus a top and "
            "bottom track (BIMSF_Description = TTOP / TBOT), or a truss "
            "assembly with Comments = TopChord / BottomChord.",
            title="UNIQUBE - Generate CNC",
        )
        return

    # Framing selected in Revit beforehand arrives already ticked.
    ticked = _preselected_names()
    if not ticked and len(units) == 1:
        ticked = set([units[0]["name"]])

    cnc_session.open_session(
        HOST_APP.uiapp,
        doc,
        units,
        _project_name(),
        _default_out_dir(),
        checked_names=ticked,
        log=logger,
        stay_open=_stay_open(),
    )


if __name__ == "__main__":
    main()
