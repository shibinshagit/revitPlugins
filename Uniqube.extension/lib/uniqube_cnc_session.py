# -*- coding: utf-8 -*-
"""Generate CNC as a tool window that stays open while Revit is used.

A window that is open beside Revit lives outside any Revit command, and
outside a command the Revit API may not be called. So nothing here touches
the model from a button click. The window asks; RevitRunner queues the work
and has Revit run it (an ExternalEvent) as soon as Revit is free.

The session also listens to the model. When framing changes in Revit the
window is told its programs are out of date, and it reads the panels again
the next time it is used, so what it shows is never an earlier state of the
model.

One window per Revit session: pressing the button again brings it forward.

Staying open needs a script engine that outlives the button script (the
bundle asks pyRevit for one). Where that is not available the same window
runs as a dialog instead, and says why.
"""
from __future__ import print_function

import json
from collections import deque

import clr

try:
    clr.AddReference("RevitAPI")
    clr.AddReference("RevitAPIUI")
except Exception:
    pass  # already loaded inside Revit; absent in the offline tests

clr.AddReference("WindowsBase")

from Autodesk.Revit import DB, UI
from System import Action, AppDomain, TimeSpan
from System.Collections.Generic import List
from System.Diagnostics import Stopwatch
from System.Windows.Threading import Dispatcher, DispatcherPriority, DispatcherTimer

import uniqube_cnc_export as cnc
import uniqube_cnc_files as files
import uniqube_cnc_window as cnc_window

SLOT = "UNIQUBE.GenerateCNC.Session"
BUDGET_MS = 120  # model work done per turn before Revit gets control back
RETRY_MS = 300
DIALOG_NOTICE = (
    "Revit is locked while this window is open, because pyRevit has not been "
    "reloaded since Generate CNC was updated. Close this window, press Reload "
    "on the pyRevit tab, then open it again - it will stay open beside Revit."
)


def engine_outlives_script(exec_params):
    """Does the engine running the button script stay alive afterwards?

    True or False when pyRevit says so, None when it cannot be told.

    A window left open beside Revit needs it (bundle.yaml asks for it with
    ``engine: persistent: true``; tools/cnc_preview_tests/bundle_check.ps1
    checks pyRevit reads that). pyRevit hands the script its engine settings
    as a JSON string, ScriptRuntimeConfigs.EngineConfigs, for example
    {"clean": false, "full_frame": false, "persistent": true}.

    pyRevit's own EXEC_PARAMS.needs_persistent_engine is not used: in pyRevit
    6.5 it reads a member the runtime does not have and always fails.
    """
    try:
        raw = exec_params.script_runtime_cfgs.EngineConfigs
    except Exception:
        return None
    try:
        if not isinstance(raw, dict):
            raw = json.loads(u"{}".format(raw))
        value = raw.get("persistent")
    except Exception:
        return None
    if isinstance(value, bool):
        return value
    text = u"{}".format(value).strip().lower()
    if text in ("true", "false"):
        return text == "true"
    return None
# A change to any of these can change a program.
WATCHED_CATEGORIES = (
    "OST_StructuralFraming",
    "OST_StructuralFramingOpening",
    "OST_Assemblies",
)


class _RunnerHandler(UI.IExternalEventHandler):
    """Revit calls Execute, in a valid API context, after Raise()."""

    def __init__(self, drain):
        self._drain = drain

    def Execute(self, uiapp):
        try:
            self._drain()
        except Exception:
            pass  # nothing may escape into Revit

    def GetName(self):
        return "UNIQUBE Generate CNC"


class RevitRunner(object):
    """Runs model work inside Revit, on Revit's terms.

    ``submit(work, done)`` returns at once. Revit calls back when it is idle
    (not in the middle of a command, a dialog or an edit mode); the queued
    work then runs for about BUDGET_MS at a time, so a long batch does not
    freeze Revit or the window. Urgent work goes ahead of the batch.

    Create it while a Revit command is running.
    """

    deferred = True

    def __init__(self):
        self._urgent = deque()
        self._normal = deque()
        self._closed = False
        self._retry = None
        self._dispatcher = Dispatcher.CurrentDispatcher
        self._handler = _RunnerHandler(self._drain)
        self._event = UI.ExternalEvent.Create(self._handler)

    def submit(self, work, done, urgent=False):
        if self._closed:
            return
        (self._urgent if urgent else self._normal).append((work, done))
        self._raise()

    def clear(self):
        """Forget work that has not started."""
        self._urgent.clear()
        self._normal.clear()

    def close(self):
        self._closed = True
        self.clear()
        if self._retry is not None:
            self._retry.Stop()
            self._retry = None
        try:
            self._event.Dispose()
        except Exception:
            pass

    def _next(self):
        if self._urgent:
            return self._urgent.popleft()
        if self._normal:
            return self._normal.popleft()
        return None

    def _raise(self):
        if self._closed or not (self._urgent or self._normal):
            return
        try:
            answer = str(self._event.Raise())
        except Exception:
            answer = "Denied"
        if answer in ("Denied", "TimedOut"):
            self._raise_later()

    def _raise_later(self):
        if self._retry is not None:
            return
        timer = DispatcherTimer()
        timer.Interval = TimeSpan.FromMilliseconds(RETRY_MS)

        def tick(sender, args):
            timer.Stop()
            self._retry = None
            self._raise()

        timer.Tick += tick
        self._retry = timer
        timer.Start()

    def _drain(self):
        watch = Stopwatch.StartNew()
        while not self._closed:
            job = self._next()
            if job is None:
                return
            work, done = job
            try:
                result, error = work(), None
            except Exception as ex:
                result, error = None, ex
            try:
                done(result, error)
            except Exception:
                pass
            if watch.ElapsedMilliseconds >= BUDGET_MS and (self._urgent or self._normal):
                # Give Revit and the window a turn, then carry on. Raising
                # from the message loop lets waiting input be handled first.
                self._dispatcher.BeginInvoke(
                    DispatcherPriority.Background, Action(self._raise)
                )
                return


def _cut_length_mm(element):
    """Revit's own Cut Length for a framing member, or None.

    Shown beside the CSV length as an independent check on the exporter.
    """
    try:
        param = element.get_Parameter(DB.BuiltInParameter.STRUCTURAL_FRAME_CUT_LENGTH)
        if param is not None and param.HasValue:
            return param.AsDouble() * cnc.FT_TO_MM
    except Exception:
        pass
    return None


def _pick_folder():
    from pyrevit import forms

    return forms.pick_folder(title="Select folder for CNC CSV files")


def _watch_filter():
    categories = List[DB.BuiltInCategory]()
    for name in WATCHED_CATEGORIES:
        categories.Add(getattr(DB.BuiltInCategory, name))
    return DB.ElementMulticategoryFilter(categories)


class CncSession(object):
    """One Generate CNC window and its connection to one Revit model.

    Create it while a Revit command is running (the button script does).

    ``stay_open`` False gives the dialog: Revit waits until it is closed, so
    model work runs on the spot and the model cannot change underneath it.
    """

    def __init__(
        self,
        uiapp,
        doc,
        units,
        job_name,
        out_dir,
        checked_names=None,
        pick_folder=None,
        log=None,
        stay_open=True,
    ):
        self._stay_open = stay_open
        self._uiapp = uiapp
        self._doc = doc
        self._log = log
        self._attached = False
        self._attached_closing = False
        self._on_changed = self._document_changed
        self._on_closing = self._document_closing
        try:
            self._title = doc.Title
        except Exception:
            self._title = "The model"
        try:
            self._filter = _watch_filter()
        except Exception as ex:
            self._filter = None  # then any change counts
            self._note("change filter", ex)
        try:
            owner = uiapp.MainWindowHandle
        except Exception:
            owner = None

        self.runner = RevitRunner() if stay_open else cnc_window.ImmediateRunner()
        self.window = cnc_window.CncPreviewWindow(
            units,
            self._build_program,
            job_name,
            out_dir,
            checked_names=checked_names,
            on_select=self._select,
            pick_folder=pick_folder or _pick_folder,
            owner_handle=owner,
            runner=self.runner,
            rescan=self._rescan if stay_open else None,
            on_export=self._write,
            on_closed=self._window_closed,
        )
        if stay_open:
            self._attach()

    def is_for(self, doc):
        return self._same_doc(doc)

    def show(self):
        """Open the window. As a dialog this returns when it is closed."""
        if self._stay_open:
            self.window.show()
        else:
            self.window.show_dialog(notice=DIALOG_NOTICE)

    # ------------------------------------------------------------ model work
    # Called by the runner, inside Revit.
    def _require_model(self):
        if not self._doc.IsValidObject:
            raise RuntimeError("the model is no longer open in Revit")

    def _build_program(self, unit, job_name):
        """The program for one unit, with the Revit member behind each row."""
        self._require_model()
        program = cnc.build_unit_program(self._doc, unit, job_name=job_name)
        members = []
        for rec in program["records"]:
            element = rec["element"]
            members.append(
                {
                    "id": str(element.Id),
                    "ref": element.Id,
                    "cut_length": _cut_length_mm(element),
                }
            )
        return {
            "filename": program["filename"],
            "text": program["text"],
            "count": program["count"],
            "revit": members,
        }

    def _rescan(self):
        self._require_model()
        return cnc.collect_cnc_units(self._doc)

    def _select(self, element_id):
        self._require_model()
        uidoc = self._uiapp.ActiveUIDocument
        if uidoc is None or not self._same_doc(uidoc.Document):
            raise RuntimeError(
                u"{} is not the model in front in Revit - switch to it first".format(
                    self._title
                )
            )
        if self._doc.GetElement(element_id) is None:
            raise RuntimeError("that member is no longer in the model")
        ids = List[DB.ElementId]()
        ids.Add(element_id)
        uidoc.Selection.SetElementIds(ids)

    # ----------------------------------------------------------------- files
    def _write(self, result):
        return files.write_programs(result["items"], result["out_dir"], log=self._log)

    # ------------------------------------------------------ model watching
    def _same_doc(self, other):
        try:
            return other is not None and other.Equals(self._doc)
        except Exception:
            return False

    def _attach(self):
        """Listen for changes. Valid only while Revit is in an API context."""
        try:
            app = self._uiapp.Application
            app.DocumentChanged += self._on_changed
            self._attached = True
            app.DocumentClosing += self._on_closing
            self._attached_closing = True
        except Exception as ex:
            # The window still works; it just has to be re-read by hand.
            self._note("could not watch the model for changes", ex)

    def _detach(self):
        """Stop listening. Valid only while Revit is in an API context."""
        if not self._attached:
            return
        self._attached = False
        app = self._uiapp.Application
        app.DocumentChanged -= self._on_changed
        if self._attached_closing:
            self._attached_closing = False
            app.DocumentClosing -= self._on_closing

    def _touches_programs(self, args):
        if self._filter is None:
            return True
        # What a deleted element was can no longer be asked, so any deletion counts.
        if args.GetDeletedElementIds().Count:
            return True
        if args.GetAddedElementIds(self._filter).Count:
            return True
        return args.GetModifiedElementIds(self._filter).Count > 0

    def _document_changed(self, sender, args):
        try:
            if self._same_doc(args.GetDocument()) and self._touches_programs(args):
                self.window.mark_stale()
        except Exception as ex:
            self._note("change notice", ex)

    def _document_closing(self, sender, args):
        try:
            if not self._same_doc(args.Document):
                return
            self._detach()  # allowed here: Revit is raising an event
            self.window.close()
        except Exception as ex:
            self._note("closing notice", ex)

    def _window_closed(self):
        _forget(self)
        for fault in self.window.errors:
            self._note("window", fault)
        if not self._stay_open:
            return
        self.runner.clear()
        if not self._attached:
            self.runner.close()
            return

        def finished(_result, error):
            if error is not None:
                self._note("stop watching the model", error)
            self.runner.close()

        # Unsubscribing is model work too.
        self.runner.submit(self._detach, finished, urgent=True)

    def _note(self, what, detail):
        if self._log is None:
            return
        try:
            self._log.error("Generate CNC: %s: %s", what, detail)
        except Exception:
            pass


# ---------------------------------------------------------------- one window
def active_session():
    """The session whose window is open, or None."""
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
    """Bring the open Generate CNC window forward if it shows this model.

    Returns True when it did. A window left open on another model is closed.
    """
    session = active_session()
    if session is None:
        return False
    if session.is_for(doc):
        session.window.activate()
        return True
    session.window.close()
    return False


def open_session(uiapp, doc, units, job_name, out_dir, checked_names=None,
                 pick_folder=None, log=None, stay_open=True):
    """Open the Generate CNC window and return its session.

    With ``stay_open`` the window is left open beside Revit and this returns
    at once. Without it the window is a dialog and this returns on closing.
    """
    existing = active_session()
    if existing is not None:
        existing.window.close()
    session = CncSession(
        uiapp, doc, units, job_name, out_dir,
        checked_names=checked_names, pick_folder=pick_folder, log=log,
        stay_open=stay_open,
    )
    if stay_open:
        # Held here so the window outlives the button script that opened it.
        AppDomain.CurrentDomain.SetData(SLOT, session)
    session.show()
    return session
