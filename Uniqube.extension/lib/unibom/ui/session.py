# -*- coding: utf-8 -*-
"""Run BOQ as a tool window that stays open while Revit is used.

Same pattern as Generate CNC: the options window is shown with Show(), model
work (folder picker, takeoff) is handed to Revit through an ExternalEvent, and
one window is kept per session.
"""
from __future__ import print_function

from System import AppDomain

from pyrevit import revit, script

from uniqube_cnc_session import (
    DIALOG_NOTICE,
    RevitRunner,
    engine_outlives_script,
)
from uniqube_cnc_window import ImmediateRunner

from unibom import collect, engine, report
from unibom.ui import dialogs, selection

SLOT = "UNIQUBE.RunBOQ.Session"

WALL_SYSTEMS = ["External wall panels", "Internal wall panels"]
SCOPE_LABEL = {
    dialogs.SCOPE_MODEL: "Whole model",
    dialogs.SCOPE_VIEW: "Active view",
    dialogs.SCOPE_SELECTION: "Selection",
}
METHOD_LABEL = {
    dialogs.METHOD_COUNTED: "Counted",
    dialogs.METHOD_RECIPE: "Recipe estimate",
    dialogs.METHOD_BOTH: "Both, compared",
}


class BoqSession(object):
    """One Run BOQ window and its connection to one Revit model."""

    def __init__(self, uiapp, doc, view, selected_ids, stay_open=True, log=None):
        self._uiapp = uiapp
        self._doc = doc
        self._view = view
        self._selected_ids = list(selected_ids or [])
        self._stay_open = stay_open
        self._log = log
        self.runner = RevitRunner() if stay_open else ImmediateRunner()
        try:
            owner = uiapp.MainWindowHandle
        except Exception:
            owner = None
        self.window = dialogs.RunBoqDialog(
            doc.Title,
            view.Name if view else "",
            len(self._selected_ids),
            report.default_folder(),
            owner_handle=owner,
            runner=self.runner,
            on_run=self.takeoff,
            on_closed=self._window_closed,
            stay_open=stay_open,
        )

    def is_for(self, doc):
        try:
            return doc is not None and doc.Equals(self._doc)
        except Exception:
            return False

    def show(self):
        if self._stay_open:
            self.window.show()
        else:
            notice = DIALOG_NOTICE.replace("Generate CNC", "Run BOQ")
            self.window.show_dialog(notice=notice)

    def takeoff(self, options):
        """Read the model and write the BOQ. Runs inside Revit."""
        if not self._doc.IsValidObject:
            raise RuntimeError("the model is no longer open in Revit")
        lib = engine.Library()
        if options.scope == dialogs.SCOPE_VIEW:
            members, walls = collect.collect(
                self._doc, "view", view=self._view)
        elif options.scope == dialogs.SCOPE_SELECTION:
            members, walls = collect.collect(
                self._doc, "selection", selected_ids=self._selected_ids)
        else:
            members, walls = collect.collect(self._doc, "model")

        counted = recipe = None
        issues = []
        if options.method in (dialogs.METHOD_COUNTED, dialogs.METHOD_BOTH):
            issues, counted = engine.check_model(members, walls, lib)
        if options.method in (dialogs.METHOD_RECIPE, dialogs.METHOD_BOTH):
            recipe = engine.run_recipes(walls, lib)
            issues = issues + recipe.issues

        when = report.stamp()
        files, error = [], None
        html_path = None
        try:
            for takeoff in (counted, recipe):
                if takeoff and options.csv:
                    files.append(report.write_csv(
                        takeoff, self._doc.Title, options.folder, when))
            if options.html:
                html_path = report.output_path(
                    options.folder, self._doc.Title, options.method, "html", when)
                files.append(html_path)
        except Exception as ex:
            error = ex

        chips = [
            ("Scope", SCOPE_LABEL[options.scope]),
            ("Method", METHOD_LABEL[options.method]),
            ("Elements", "{:,}".format(len(members))),
            ("Walls", "{:,}".format(len(walls))),
            ("Rules", "v{}".format(lib.settings.get("version", 1))),
        ]

        def build(link):
            return report.boq_report(
                self._doc.Title, chips, counted, recipe, issues,
                link=link, files=files, wall_systems=WALL_SYSTEMS)

        if html_path:
            try:
                build(None).save(html_path)
            except Exception as ex:
                error = ex

        output = script.get_output()
        output.set_title("UNIBOM - BOQ")
        uidoc = self._uiapp.ActiveUIDocument
        links = selection.SelectionLinks(output, uidoc or revit.uidoc)
        rep = build(links.link)
        if error is not None:
            rep.tab("files", "Files")
            rep.add(rep.note(u"Could not save files: {}".format(report.esc(error))))
        rep.show(output)

        summary = "{}: {:,} elements, {:,} walls".format(
            METHOD_LABEL[options.method], len(members), len(walls))
        if files:
            summary += "\n{} file{} written to {}".format(
                len(files), "" if len(files) == 1 else "s", options.folder)
        if error is not None:
            summary += u"\nCould not save every file: {}".format(error)
        return {
            "ok": error is None,
            "title": "Takeoff finished" if error is None else "Takeoff finished, with problems",
            "summary": summary,
            "folder": options.folder,
            "files": list(files),
        }

    def _window_closed(self):
        _forget(self)
        if self._stay_open and hasattr(self.runner, "close"):
            try:
                self.runner.clear()
                self.runner.close()
            except Exception:
                pass
        if self._log is None:
            return
        for fault in getattr(self.window, "errors", []) or []:
            try:
                self._log.error("Run BOQ: %s", fault)
            except Exception:
                pass


def active_session():
    try:
        session = AppDomain.CurrentDomain.GetData(SLOT)
        if session is not None and session.window.is_open:
            return session
    except Exception:
        pass
    return None


def _forget(session):
    try:
        if AppDomain.CurrentDomain.GetData(SLOT) is session:
            AppDomain.CurrentDomain.SetData(SLOT, None)
    except Exception:
        pass


def reuse_session(doc):
    session = active_session()
    if session is None:
        return False
    if session.is_for(doc):
        session.window.activate()
        return True
    session.window.close()
    return False


def open_session(uiapp, doc, view, selected_ids, stay_open=True, log=None):
    existing = active_session()
    if existing is not None:
        existing.window.close()
    session = BoqSession(
        uiapp, doc, view, selected_ids, stay_open=stay_open, log=log)
    if stay_open:
        AppDomain.CurrentDomain.SetData(SLOT, session)
    session.show()
    return session


def stay_open_from_exec(exec_params):
    return engine_outlives_script(exec_params) is not False
