# -*- coding: utf-8 -*-
"""Run the real Generate CNC button script end to end, outside Revit.

Run through run_window_check.ps1 / netcore_host with this file as the driver.

Real: script.py, uniqube_cnc_session (the runner that hands model work to
Revit, the model watching, the single window), uniqube_cnc_window,
uniqube_cnc_preview, uniqube_cnc_files, the XAML, and the file writing.

Stand-ins: pyrevit, the Revit API, and the two exporter calls that need a
Revit model (the unit scan and the program builder), which hand back the
production CSV files.

The stand-in Revit keeps Revit's rule: the model may be touched only while
Revit is running a command, an external event or one of its own events.
Every stand-in model call records a violation if it is reached any other
way, so a window that calls the model straight from a button click fails
here the way it would misbehave in Revit.
"""
import sys

import clr

clr.AddReference(MODULES)
from IronPython.Modules import ResourceMetaPathImporter

sys.meta_path.append(
    ResourceMetaPathImporter(LOADER, "pyRevitLoader.python_2712pr_lib.zip")
)
sys.path.insert(0, LIB)

import os
import shutil
import types

clr.AddReference("WindowsBase")
clr.AddReference("PresentationFramework")
import System
from System import Action, Int32
from System.IO import File
from System.Reflection import AssemblyName, TypeAttributes
from System.Reflection.Emit import AssemblyBuilder, AssemblyBuilderAccess
from System.Threading import Thread
from System.Windows.Threading import Dispatcher, DispatcherPriority

FAILURES = 0


def check(label, condition, detail=""):
    global FAILURES
    if condition:
        print("  ok    " + label)
    else:
        FAILURES += 1
        print("  FAIL  " + label + ("  -> " + str(detail) if detail else ""))


ELB = os.path.join(SAMPLES, "ELB-1_600S162-54(50)_.csv")
RT = os.path.join(SAMPLES, "RT-1_362S162-54(50)_.csv")
RUN_DIR = os.path.join(OUT, "script_run")
if os.path.isdir(RUN_DIR):
    shutil.rmtree(RUN_DIR)
os.makedirs(RUN_DIR)

# ------------------------------------------------------------ stand-in Revit
LOG = []
ALERTS = []
SELECTED = []
EVENTS = []
OUTSIDE = []  # model calls made outside an API context
REVIT = {"in_api": False, "busy": False, "raises": 0, "executes": 0}
DISPATCHER = Dispatcher.CurrentDispatcher


def api(what):
    """Every stand-in model call passes through here."""
    if not REVIT["in_api"]:
        OUTSIDE.append(what)


class in_revit(object):
    """A stretch during which Revit allows API calls."""

    def __enter__(self):
        self.before = REVIT["in_api"]
        REVIT["in_api"] = True

    def __exit__(self, *args):
        REVIT["in_api"] = self.before
        return False


class _FakeExternalEvent(object):
    """Revit's half of an ExternalEvent: Execute comes later, when it is idle."""

    def __init__(self, handler):
        self.handler = handler
        self.pending = False
        self.disposed = False
        EVENTS.append(self)

    def Raise(self):
        REVIT["raises"] += 1
        if self.disposed:
            return "Denied"
        if self.pending:
            return "Pending"
        self.pending = True
        if not REVIT["busy"]:
            self.post()
        return "Accepted"

    def post(self):
        DISPATCHER.BeginInvoke(DispatcherPriority.ApplicationIdle, Action(self.fire))

    def fire(self):
        if not self.pending or self.disposed or REVIT["busy"]:
            return
        self.pending = False
        REVIT["executes"] += 1
        with in_revit():
            self.handler.Execute(None)

    def Dispose(self):
        self.disposed = True


class _ExternalEvent(object):
    Create = staticmethod(_FakeExternalEvent)


class _Event(object):
    """A Revit event: subscribing needs an API context, and firing is one."""

    def __init__(self, name):
        self.name = name
        self.handlers = []

    def __iadd__(self, handler):
        api(self.name + " +=")
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        api(self.name + " -=")
        self.handlers.remove(handler)
        return self

    def fire(self, args):
        with in_revit():
            for handler in list(self.handlers):
                handler(None, args)


class _Param(object):
    def __init__(self, value):
        self.HasValue = value is not None
        self._value = value

    def AsDouble(self):
        return self._value

    def AsString(self):
        return self._value


class _Element(object):
    def __init__(self, number, length_mm):
        self.Id = number
        self._length = length_mm

    def get_Parameter(self, _which):
        return _Param(self._length / 304.8)


class _ProjectInfo(object):
    def LookupParameter(self, name):
        return _Param("ZUHA-ADMIN BUILDING")


class _Doc(object):
    def __init__(self, title):
        self.Title = title
        self.PathName = os.path.join(RUN_DIR, title + ".rvt")
        self.ProjectInformation = _ProjectInfo()
        self.IsValidObject = True

    def Equals(self, other):
        return other is self

    def GetElement(self, element_id):
        api("GetElement")
        return _Element(element_id, 0.0)


class _Selection(object):
    element_ids = []

    def SetElementIds(self, ids):
        api("SetElementIds")
        SELECTED.append(list(ids))


class _UiDoc(object):
    def __init__(self, document):
        self.Document = document
        self.Selection = _Selection()


class _App(object):
    def __init__(self):
        self.DocumentChanged = _Event("DocumentChanged")
        self.DocumentClosing = _Event("DocumentClosing")


class _UiApp(object):
    def __init__(self, app, uidoc):
        self.Application = app
        self.ActiveUIDocument = uidoc


class _Ids(object):
    def __init__(self, count):
        self.Count = count


class _Changed(object):
    """DocumentChangedEventArgs: counts of framing and of other elements."""

    FILTERS = []

    def __init__(self, document, framing_added=0, framing_modified=0, other=0, deleted=0):
        self.document = document
        self.framing_added = framing_added
        self.framing_modified = framing_modified
        self.other = other
        self.deleted = deleted

    def GetDocument(self):
        return self.document

    def GetDeletedElementIds(self):
        return _Ids(self.deleted)

    def GetAddedElementIds(self, element_filter=None):
        _Changed.FILTERS.append(element_filter)
        return _Ids(self.framing_added + (0 if element_filter else self.other))

    def GetModifiedElementIds(self, element_filter=None):
        _Changed.FILTERS.append(element_filter)
        return _Ids(self.framing_modified + (0 if element_filter else self.other))


class _Closing(object):
    def __init__(self, document):
        self.Document = document


class _Filter(object):
    def __init__(self, categories):
        self.categories = sorted(str(category) for category in categories)


class _Progress(object):
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Logger(object):
    def error(self, *args):
        LOG.append(("error",) + args)

    def info(self, *args):
        LOG.append(("info",) + args)


def _module(name, **members):
    module = types.ModuleType(name)
    for key, value in members.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class _BuiltInParameter(object):
    STRUCTURAL_FRAME_CUT_LENGTH = "STRUCTURAL_FRAME_CUT_LENGTH"


def _category_enum():
    """A real .NET enum, so List[BuiltInCategory] works as it does in Revit."""
    assembly = AssemblyBuilder.DefineDynamicAssembly(
        AssemblyName("StandInRevit"), AssemblyBuilderAccess.Run
    )
    builder = assembly.DefineDynamicModule("StandInRevit").DefineEnum(
        "BuiltInCategory", TypeAttributes.Public, Int32
    )
    for value, name in enumerate(
        ("OST_StructuralFraming", "OST_StructuralFramingOpening", "OST_Assemblies",
         "OST_Walls")
    ):
        builder.DefineLiteral(name, Int32(value + 1))
    return clr.GetPythonType(builder.CreateTypeInfo().AsType())


DOC = _Doc("model")
OTHER_DOC = _Doc("another model")
UIDOC = _UiDoc(DOC)
APP = _App()
UIAPP = _UiApp(APP, UIDOC)

FAKE_DB = _module(
    "Autodesk.Revit.DB",
    BuiltInParameter=_BuiltInParameter,
    BuiltInCategory=_category_enum(),
    ElementId=System.Object,
    ElementMulticategoryFilter=_Filter,
)
FAKE_UI = _module(
    "Autodesk.Revit.UI",
    IExternalEventHandler=object,
    ExternalEvent=_ExternalEvent,
)
_module("Autodesk", Revit=_module("Autodesk.Revit", DB=FAKE_DB, UI=FAKE_UI))

HOST_APP = types.ModuleType("HOST_APP")
HOST_APP.uiapp = UIAPP


class _RuntimeConfigs(object):
    """ScriptRuntimeConfigs: the engine settings arrive as a JSON string."""

    def __init__(self, persistent):
        self.EngineConfigs = (
            '{"clean": false, "full_frame": false, "persistent": %s}'
            % ("true" if persistent else "false")
        )


class _ExecParams(object):
    """pyRevit 6.5's EXEC_PARAMS, as far as the button script is concerned."""

    def __init__(self):
        self.script_runtime_cfgs = _RuntimeConfigs(True)  # as bundle.yaml asks

    @property
    def needs_persistent_engine(self):
        # The real one reads ScriptRuntime.EngineConfigs, which does not
        # exist, so it fails whatever the bundle says. Not to be relied on.
        raise AttributeError("'ScriptRuntime' object has no attribute 'EngineConfigs'")


EXEC_PARAMS = _ExecParams()
REVIT_MODULE = _module(
    "pyrevit.revit",
    doc=DOC,
    uidoc=UIDOC,
    get_selection=lambda: _Selection(),
)
_module(
    "pyrevit",
    DB=FAKE_DB,
    HOST_APP=HOST_APP,
    EXEC_PARAMS=EXEC_PARAMS,
    revit=REVIT_MODULE,
    forms=_module(
        "pyrevit.forms",
        ProgressBar=_Progress,
        alert=lambda *a, **k: ALERTS.append(a),
        pick_folder=lambda **k: None,
    ),
    script=_module(
        "pyrevit.script",
        get_logger=lambda: _Logger(),
    ),
)

# --------------------------------------------------- exporter calls on Revit
import uniqube_cnc_export as cnc
import uniqube_cnc_preview as preview
import uniqube_cnc_session as cnc_session
import uniqube_cnc_window as cnc_window

UNITS = [
    {"name": "ELB-1", "members": [None] * 7, "csv": ELB},
    {"name": "ELB-2", "members": [None] * 7, "csv": ELB},  # identical program
    {"name": "RT-1", "members": [None] * 47, "csv": RT},
    {"name": "WP-9", "members": [], "csv": None},
]
BUILDS = []
SCANS = []


def fake_collect_cnc_units(doc):
    api("collect_cnc_units")
    SCANS.append(doc.Title)
    return list(UNITS)


def fake_build_unit_program(doc, unit, job_name=None):
    api("build_unit_program")
    BUILDS.append(unit["name"])
    if unit["csv"] is None:
        raise ValueError("no exportable framing in {}".format(unit["name"]))
    text = File.ReadAllText(unit["csv"])
    lines = text.split("\r\n")
    lines[0] = "DETAILS,,{}".format(job_name or "")
    text = "\r\n".join(lines)
    components = preview.parse_program(text)["components"]
    return {
        "filename": cnc.suggest_filename(unit["name"], components[0]["section"]),
        "text": text,
        "count": len(components),
        "records": [
            {"element": _Element(500000 + c["index"], c["length"])} for c in components
        ],
    }


cnc.collect_cnc_units = fake_collect_cnc_units
cnc.unit_names_for_elements = lambda doc, elements: set()
cnc.build_unit_program = fake_build_unit_program

SCRIPT = os.path.join(
    os.path.dirname(LIB),
    "UNIQUBE.tab", "Structural.panel", "GenerateCNC.pushbutton", "script.py",
)


def press_button(document=DOC):
    """The user clicks Generate CNC: pyRevit runs script.py as a command."""
    REVIT_MODULE.doc = document
    UIAPP.ActiveUIDocument = UIDOC if document is DOC else _UiDoc(document)
    with in_revit():
        execfile(SCRIPT, {"__name__": "__main__", "__file__": SCRIPT})


def settle():
    DISPATCHER.Invoke(DispatcherPriority.ApplicationIdle, Action(lambda: None))


def idle():
    """Let the stand-in Revit reach idle until nothing more is waiting."""
    for _ in range(400):
        settle()
        if REVIT["busy"] or not any(e.pending for e in EVENTS if not e.disposed):
            break
    settle()
    settle()


def revit_free():
    REVIT["busy"] = False
    for event in EVENTS:
        if event.pending and not event.disposed:
            event.post()


def visible(element):
    return str(element.Visibility) == "Visible"


print("IronPython {} on .NET {}".format(sys.version.split(" (")[0], System.Environment.Version))

# -------------------------------------------------------------------- open
print("press the button")
press_button()
session = cnc_session.active_session()
check("the script returns and leaves the window open",
      session is not None and session.window.window.IsVisible)
tool = session.window
tool._in_use = lambda: False  # Revit, not this window, is being worked in
check("the model was scanned once, inside the command", SCANS == ["model"], SCANS)
check("no program is built during the click", BUILDS == [], BUILDS)
idle()
check("the first unit is drawn once Revit is idle",
      tool._current == 0 and tool._entry_now is not None and BUILDS == ["ELB-1"], BUILDS)
check("job name comes from Project Information", tool.job_box.Text == "ZUHA-ADMIN BUILDING",
      tool.job_box.Text)
CNC_DIR = os.path.join(RUN_DIR, "CNC")
check("folder is <model folder>\\CNC", tool.folder_text.Text == CNC_DIR, tool.folder_text.Text)
check("Revit cut lengths reach the window", tool.revit_check_text.Text.startswith(
    "Length against Revit Cut Length: 7 of 7"), tool.revit_check_text.Text)
check("the footer button reads Close", tool.cancel_button.Content == "Close")

print("Select in Revit")
tool._select(0)
check("member line shows the Revit element", "500000" in tool.member_revit.Text,
      tool.member_revit.Text)
tool._on_select_in_revit(None, None)
check("the click itself does not touch Revit", SELECTED == [], SELECTED)
idle()
check("Revit makes the selection when idle", SELECTED == [[500000]]
      and "selected in Revit" in tool.status_text.Text, (SELECTED, tool.status_text.Text))
UIAPP.ActiveUIDocument = _UiDoc(OTHER_DOC)
tool._on_select_in_revit(None, None)
idle()
check("with another model in front it says so instead",
      SELECTED == [[500000]] and "is not the model in front" in tool.status_text.Text,
      tool.status_text.Text)
UIAPP.ActiveUIDocument = UIDOC

print("Revit is busy")
REVIT["busy"] = True
cnc_window.SLOW_AFTER_S = 0.05
tool.unit_list.SelectedItem = tool._row_view(2)
for _ in range(25):
    Thread.CurrentThread.Join(10)
    settle()
check("the program waits, and the window says why",
      tool._entry_now is None and "Waiting for Revit" in tool.empty_text.Text
      and "RT-1" not in BUILDS, tool.empty_text.Text)
cnc_window.SLOW_AFTER_S = 2.5
revit_free()
idle()
check("it arrives as soon as Revit is free",
      tool.view_title.Text == "RT-1" and tool.metric_components.Text == "47",
      tool.view_title.Text)

print("press the button again")
press_button()
check("the same window comes forward", cnc_session.active_session() is session
      and len([e for e in EVENTS if not e.disposed]) == 1)
check("the model is not scanned again", SCANS == ["model"], SCANS)

print("the model changes in Revit")
APP.DocumentChanged.fire(_Changed(OTHER_DOC, framing_modified=3))
check("a change in another model is ignored", not tool._stale and not visible(tool.model_bar))
APP.DocumentChanged.fire(_Changed(DOC, other=5))
check("a change that is not framing is ignored", not tool._stale and not visible(tool.model_bar))
check("framing, framing openings and assemblies are watched",
      _Changed.FILTERS and all(f is not None and f.categories == [
          "OST_Assemblies", "OST_StructuralFraming", "OST_StructuralFramingOpening"]
          for f in _Changed.FILTERS), [f and f.categories for f in _Changed.FILTERS])
APP.DocumentChanged.fire(_Changed(DOC, framing_modified=1))
check("changed framing marks the window out of date",
      tool._stale and visible(tool.model_bar) and "has changed" in tool.model_bar_text.Text,
      tool.model_bar_text.Text)
check("nothing is read while Revit is being worked in", SCANS == ["model"], SCANS)
UNITS.append({"name": "ELB-3", "members": [None] * 7, "csv": ELB})
tool._on_activated(None, None)
idle()
check("coming back reads the panels again",
      SCANS == ["model", "model"] and tool.unit_list.Items.Count == 5
      and not visible(tool.model_bar), (SCANS, tool.unit_list.Items.Count))
check("and shows the same unit, rebuilt", tool.view_title.Text == "RT-1"
      and BUILDS.count("RT-1") == 2, BUILDS)
APP.DocumentChanged.fire(_Changed(DOC, deleted=1))
check("a deletion counts too", tool._stale)
tool._on_activated(None, None)
idle()
APP.DocumentChanged.fire(_Changed(DOC, framing_added=2))
check("and so does new framing", tool._stale)
tool._on_activated(None, None)
idle()

print("work is handed to Revit in turns")
cnc_session.BUDGET_MS = 0  # one program per turn
executes = REVIT["executes"]
tool._on_verify(None, None)
for _ in range(2000):
    if tool._verify_timer is None:
        break
    Thread.CurrentThread.Join(2)
    idle()
turns = REVIT["executes"] - executes
check("every unit gets a status", all(tool._unit_row(i)["Status"] for i in range(5)),
      [tool._unit_row(i)["Status"] for i in range(5)])
check("checks are summarised", tool.verify_text.Text == "4 passed, 0 with warnings, 1 with errors",
      tool.verify_text.Text)
check("Revit gets control back between programs", turns >= 4, turns)
cnc_session.BUDGET_MS = 120

print("export")
del BUILDS[:]
for index in (0, 1, 2):
    tool._unit_row(index)["Checked"] = True
tool._update_checked_count()
check("export button counts the ticks", tool.export_button.Content == "Export 3 programs",
      tool.export_button.Content)
tool._on_export(None, None)
idle()
files = sorted(os.listdir(CNC_DIR))
check("identical panels share one file", files == [
    "ELB-1_600S162-54(50)_.csv", "RT-1_362S162-54(50)_.csv"], files)
for name, source in (("ELB-1_600S162-54(50)_.csv", ELB), ("RT-1_362S162-54(50)_.csv", RT)):
    path = os.path.join(CNC_DIR, name)
    if os.path.isfile(path):
        with open(path, "rb") as handle:
            written = handle.read()
        with open(source, "rb") as handle:
            original = handle.read()
        check(name + " is byte-identical to the production file", written == original,
              (len(written), len(original)))
check("what was checked is what was written, nothing is built twice", BUILDS == [], BUILDS)
check("the window stays open and shows the report",
      tool.is_open and visible(tool.report_overlay)
      and tool.report_title.Text == "Export finished", tool.report_title.Text)
check("the report lists both files and the repeat",
      "ELB-1_600S162-54(50)_.csv" in tool.report_details.Text
      and "RT-1_362S162-54(50)_.csv" in tool.report_details.Text
      and "x2 identical units" in tool.report_details.Text, tool.report_details.Text)
tool._hide_report()

print("close the window")
check("the window is listening to the model",
      len(APP.DocumentChanged.handlers) == 1 and len(APP.DocumentClosing.handlers) == 1)
tool.window.Close()
settle()
check("the session is forgotten at once", cnc_session.active_session() is None)
idle()
check("Revit is asked to stop the notices, and does",
      APP.DocumentChanged.handlers == [] and APP.DocumentClosing.handlers == [],
      (APP.DocumentChanged.handlers, APP.DocumentClosing.handlers))
check("the external event is released", all(e.disposed for e in EVENTS), len(EVENTS))
check("no window fault was logged", not [e for e in LOG if e[0] == "error"], LOG)

print("the model is closed in Revit")
press_button()
second = cnc_session.active_session()
check("pressing the button opens a new window", second is not None and second is not session
      and SCANS[-1] == "model" and len(SCANS) == 5, SCANS)
idle()
APP.DocumentClosing.fire(_Closing(OTHER_DOC))
check("closing another model leaves the window alone", second.window.is_open)
DOC.IsValidObject = False  # the model went away without a notice
second.window.unit_list.SelectedItem = second.window._row_view(2)
idle()
check("a model that is gone is reported, not read",
      "no longer open in Revit" in second.window.empty_text.Text, second.window.empty_text.Text)
DOC.IsValidObject = True
APP.DocumentClosing.fire(_Closing(DOC))
settle()
check("closing its model closes the window", not second.window.is_open
      and cnc_session.active_session() is None)
check("and the notices stop there and then",
      APP.DocumentChanged.handlers == [] and APP.DocumentClosing.handlers == [])

print("another model")
press_button()
third = cnc_session.active_session()
idle()
press_button(OTHER_DOC)
fourth = cnc_session.active_session()
check("pressing the button in another model replaces the window",
      not third.window.is_open and fourth is not None and fourth is not third
      and fourth.is_for(OTHER_DOC) and SCANS[-1] == "another model", SCANS)
idle()
fourth.window.window.Close()
idle()
check("nothing is left behind", APP.DocumentChanged.handlers == []
      and all(e.disposed for e in EVENTS) and cnc_session.active_session() is None)

print("what pyRevit says about the engine")


class _Says(object):
    def __init__(self, configs):
        self.script_runtime_cfgs = configs


class _Raw(object):
    def __init__(self, text):
        self.EngineConfigs = text


outlives = cnc_session.engine_outlives_script
check("kept alive: the window stays open", outlives(_Says(_RuntimeConfigs(True))) is True)
check("not kept alive: the dialog", outlives(_Says(_RuntimeConfigs(False))) is False)
check("the script reads the setting pyRevit really provides",
      outlives(EXEC_PARAMS) is True)
for label, params in (
    ("no runtime (run outside a pyRevit command)", _Says(None)),
    ("no engine settings", _Says(_Raw(None))),
    ("settings that are not JSON", _Says(_Raw("persistent"))),
    ("settings without the key", _Says(_Raw('{"clean": true}'))),
    ("an EXEC_PARAMS of another shape", object()),
):
    check("{}: cannot be told".format(label), outlives(params) is None, outlives(params))

print("pyRevit not reloaded yet: the engine ends with the script")
EXEC_PARAMS.script_runtime_cfgs = _RuntimeConfigs(False)
DIALOG = {}
events_before = len(EVENTS)
rt_file = os.path.join(CNC_DIR, "RT-1_362S162-54(50)_.csv")
os.remove(rt_file)
show_dialog = cnc_window.CncPreviewWindow.show_dialog


def watched_show_dialog(self, notice=None):
    """Look at the dialog while it is open, export from it, then close it."""

    def look():
        try:
            DIALOG["notice"] = self.model_bar_text.Text if visible(self.model_bar) else ""
            DIALOG["drawn"] = self._entry_now is not None
            DIALOG["button"] = self.cancel_button.Content
            DIALOG["reread"] = visible(self.refresh_button)
            DIALOG["watching"] = len(APP.DocumentChanged.handlers)
            DIALOG["kept"] = cnc_session.active_session()
            self._unit_row(2)["Checked"] = True
            self._update_checked_count()
            self._on_export(None, None)
            DIALOG["report"] = visible(self.report_overlay) and self.report_title.Text
            DIALOG["open"] = self.is_open
            DIALOG["errors"] = list(self.errors)
        finally:
            self.window.Close()

    DISPATCHER.BeginInvoke(DispatcherPriority.ApplicationIdle, Action(look))
    return show_dialog(self, notice)


cnc_window.CncPreviewWindow.show_dialog = watched_show_dialog
press_button()
cnc_window.CncPreviewWindow.show_dialog = show_dialog
check("the window runs as a dialog and says how to change that",
      "Reload" in DIALOG.get("notice", "") and "pyRevit" in DIALOG.get("notice", ""),
      DIALOG.get("notice"))
check("it works as before: the program is drawn", DIALOG.get("drawn") is True, DIALOG)
check("Export writes the file and reports, without closing",
      os.path.isfile(rt_file) and DIALOG.get("report") == "Export finished"
      and DIALOG.get("open") is True and DIALOG.get("button") == "Close", DIALOG)
check("nothing is left running for a dialog",
      len(EVENTS) == events_before and DIALOG.get("watching") == 0
      and DIALOG.get("kept") is None and DIALOG.get("reread") is False
      and cnc_session.active_session() is None, DIALOG)
check("no handler raised in the dialog", DIALOG.get("errors") == [], DIALOG.get("errors"))

print("pyRevit cannot tell: do what the bundle asks")
EXEC_PARAMS.script_runtime_cfgs = None
press_button()
unsure = cnc_session.active_session()
check("the window stays open beside Revit",
      unsure is not None and unsure.window.is_open
      and not visible(unsure.window.model_bar), unsure)
idle()
unsure.window.window.Close()
idle()
EXEC_PARAMS.script_runtime_cfgs = _RuntimeConfigs(True)
check("and closes cleanly", cnc_session.active_session() is None
      and APP.DocumentChanged.handlers == [] and all(e.disposed for e in EVENTS))

print("the rule Revit enforces")
check("the model was only ever touched when Revit allowed it", OUTSIDE == [], OUTSIDE)
check("no errors logged", not [entry for entry in LOG if entry[0] == "error"], LOG)
check("no alerts raised", ALERTS == [], ALERTS)
for each in (session, second, third, fourth, unsure):
    check("no handler raised", each.window.errors == [], each.window.errors)

print("")
print("FAILURES: {}".format(FAILURES))
