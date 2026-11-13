# -*- coding: utf-8 -*-
"""UNIBOM Run BOQ: take off quantities into a BOQ report, CSV and HTML.

The window stays open beside Revit. This script only opens it; from then on
unibom.ui.session keeps it alive and has Revit do the takeoff when asked.
Pressing the button again brings the same window forward.
"""
from __future__ import print_function

from pyrevit import EXEC_PARAMS, HOST_APP, revit, script

from unibom import collect
from unibom.ui import session as boq_session

doc = revit.doc
logger = script.get_logger()


def main():
    if boq_session.reuse_session(doc):
        return

    selected = [collect.eid(i) for i in revit.get_selection().element_ids]
    view = revit.active_view
    boq_session.open_session(
        HOST_APP.uiapp,
        doc,
        view,
        selected,
        stay_open=boq_session.stay_open_from_exec(EXEC_PARAMS),
        log=logger,
    )


if __name__ == "__main__":
    main()
