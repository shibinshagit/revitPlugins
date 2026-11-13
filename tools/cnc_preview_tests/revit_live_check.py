# -*- coding: utf-8 -*-
"""Check the Generate CNC window inside a running Revit, on the open model.

The window stays open beside Revit and has Revit do its model work when
Revit is idle. Revit is only idle between calls, so this check is a series
of short steps, each executed in Revit's Python context (pyRevit routes /
Revit MCP), which provides doc, uidoc and DB:

    g = dict(globals()); g["OUT"] = r"...\\out_revit"; g["STEP"] = "open"
    execfile(r"...\\tools\\cnc_preview_tests\\revit_live_check.py", g)

Steps, in order (STEP = ...):

    open       scan the model and open the window, as the button does
    state      report what the window shows; NAME saves a screenshot
    show       ask for UNIT (a panel / truss name) in the viewer
    select     select row 1 of the shown program in Revit
    selection  report what is selected in Revit now
    verify     run the checks on every program
    changed    tell the window the model changed, then come back to it
    export     tick the shown unit and export it to OUT
    close      close the window and put the Revit selection back

What "state" reports after a step shows whether Revit ran the work the
window asked for: nothing a step asks of the model happens inside that step.

Read-only on the model: no transaction is opened. Files are written only to
OUT (screenshots, and the CSV of the "export" step).

BEFORE EVERY STEP make sure Revit can take the request: run revit_windows.ps1
and send nothing unless Revit's main window reads enabled=True and no command
is in progress there. A request sent while Revit is blocked (a dialog is
open) waits, the caller gives up after 60 s, and when Revit later answers the
abandoned request pyRevit's request server fails on its background thread
and Revit exits. That happened on 13 Nov 2026. If a step times out, stop,
send nothing more, and have the model saved before Revit is used again.
"""
from __future__ import print_function

import os
import sys

import clr

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
LIB = os.path.join(REPO, "Uniqube.extension", "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System import AppDomain
from System.Collections.Generic import List
from System.IO import FileMode, FileStream
from System.Windows.Media import PixelFormats
from System.Windows.Media.Imaging import BitmapFrame, PngBitmapEncoder, RenderTargetBitmap

STEP = globals().get("STEP", "state")
SELECTION_SLOT = "UNIQUBE.GenerateCNC.LiveCheck.Selection"
MODULES = (
    "uniqube_cnc_export", "uniqube_cnc_preview", "uniqube_cnc_files",
    "uniqube_cnc_window", "uniqube_cnc_session",
)


def shot(win, name):
    root = win.Content
    bitmap = RenderTargetBitmap(
        int(root.ActualWidth), int(root.ActualHeight), 96.0, 96.0, PixelFormats.Pbgra32
    )
    bitmap.Render(root)
    encoder = PngBitmapEncoder()
    encoder.Frames.Add(BitmapFrame.Create(bitmap))
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    stream = FileStream(os.path.join(OUT, name + ".png"), FileMode.Create)
    try:
        encoder.Save(stream)
    finally:
        stream.Close()
    print("screenshot: {}.png".format(name))


def report(tool):
    entry = tool._entry_now
    print("window open: {}   visible: {}".format(tool.is_open, tool.window.IsVisible))
    print("units listed: {}   ticked: {}".format(
        tool.unit_list.Items.Count, len(tool._checked_indexes())))
    print("viewer title: {}".format(tool.view_title.Text))
    if entry is None:
        print("viewer: {}".format(tool.empty_text.Text.replace("\n", " ")))
    elif entry["error"]:
        print("program error: {}".format(entry["error"]))
    else:
        print("program: {} components, {} operations, file {}".format(
            tool.metric_components.Text, tool.metric_operations.Text, entry["filename"]))
        print("checks: {}".format(tool.check_title.Text))
        print("revit: {}".format(tool.revit_check_text.Text))
        print("drawing elements: {}".format(tool.scene.Children.Count))
    print("selected row: {}".format(tool._selected))
    print("status: {}".format(tool.status_text.Text))
    print("left note: {}".format(tool.verify_text.Text))
    if str(tool.model_bar.Visibility) == "Visible":
        print("bar: {}".format(tool.model_bar_text.Text))
    if str(tool.report_overlay.Visibility) == "Visible":
        print("report: {} | {}".format(
            tool.report_title.Text, tool.report_summary.Text.replace("\n", " | ")))
    statuses = {}
    for index in range(len(tool._units)):
        flag = tool._unit_row(index)["Status"] or "not built"
        statuses[flag] = statuses.get(flag, 0) + 1
    print("unit statuses: {}".format(statuses))
    print("handler faults: {}".format(tool.errors))


if STEP == "open":
    for name in MODULES:
        previous = sys.modules.get(name)
        if name == "uniqube_cnc_session" and previous is not None:
            running = previous.active_session()
            if running is not None:
                running.window.close()
        sys.modules.pop(name, None)
    import uniqube_cnc_export as cnc
    import uniqube_cnc_session as cnc_session

    uiapp = uidoc.Application
    AppDomain.CurrentDomain.SetData(
        SELECTION_SLOT, List[DB.ElementId](uidoc.Selection.GetElementIds())
    )
    units = cnc.collect_cnc_units(doc)
    print("units found: {}".format(len(units)))
    session = cnc_session.open_session(
        uiapp, doc, units, "LIVE CHECK", OUT, checked_names=[units[0]["name"]]
    )
    tool = session.window
    print("window visible on return: {}".format(tool.window.IsVisible))
    print("built during this call: {}".format(tool._entry_now is not None))
    print("watching the model: {}   change filter: {}".format(
        session._attached, type(session._filter).__name__))
else:
    import uniqube_cnc_session as cnc_session

    session = cnc_session.active_session()
    if session is None:
        print("no Generate CNC window is open")
    else:
        tool = session.window
        if STEP == "state":
            report(tool)
            if globals().get("NAME"):
                shot(tool.window, NAME)
        elif STEP == "show":
            found = False
            for index, unit in enumerate(tool._units):
                if unit["name"] == UNIT:
                    item = tool._row_view(index)
                    tool.unit_list.SelectedItem = item
                    tool.unit_list.ScrollIntoView(item)
                    found = True
                    break
            print("asked for {}: {}".format(UNIT, found))
            print("drawn inside this call: {}".format(tool._entry_now is not None))
        elif STEP == "select":
            tool._select(0)
            info = tool._entry_now["revit"][0]
            print("row 1 is Revit element {}".format(info["id"]))
            tool._on_select_in_revit(None, None)
            print("status: {}".format(tool.status_text.Text))
            print("selected in Revit inside this call: {}".format(
                [str(i) for i in uidoc.Selection.GetElementIds()] == [info["id"]]))
        elif STEP == "selection":
            ids = [str(i) for i in uidoc.Selection.GetElementIds()]
            print("selected in Revit: {}".format(ids))
            print("status: {}".format(tool.status_text.Text))
        elif STEP == "verify":
            tool._on_verify(None, None)
            print("checking started: {}".format(tool._verify_timer is not None))
        elif STEP == "changed":
            tool.mark_stale()
            print("bar: {}".format(tool.model_bar_text.Text))
            tool._on_activated(None, None)
            print("then: {}".format(tool.model_bar_text.Text))
        elif STEP == "export":
            for index in range(len(tool._units)):
                tool._unit_row(index)["Checked"] = index == tool._current
            tool._update_checked_count()
            print("exporting: {}".format(tool.export_button.Content))
            tool._on_export(None, None)
        elif STEP == "close":
            tool.window.Close()
            saved = AppDomain.CurrentDomain.GetData(SELECTION_SLOT)
            if saved is not None:
                uidoc.Selection.SetElementIds(saved)
                AppDomain.CurrentDomain.SetData(SELECTION_SLOT, None)
            print("closed: {}   selection put back".format(not tool.is_open))
        else:
            print("unknown STEP {}".format(STEP))
