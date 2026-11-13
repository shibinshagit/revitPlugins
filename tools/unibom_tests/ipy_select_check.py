# -*- coding: utf-8 -*-
"""Check UNIBOM's Select links under pyRevit's IronPython, inside Revit.

Hooks a real WinForms WebBrowser the way the output window is hooked, feeds a
unibom link click through the Navigating handler and confirms Revit selected
every element. Restores the user's selection afterwards. Host sets doc, uidoc,
LIB.
"""
import sys
import traceback

RESULT = []


def log(*parts):
    RESULT.append(u" ".join(u"{}".format(p) for p in parts))


try:
    if LIB not in sys.path:
        sys.path.insert(0, LIB)
    import clr
    clr.AddReference("RevitAPI")
    clr.AddReference("System.Windows.Forms")
    for name in ("System", "System.Private.Uri", "System.Runtime"):
        try:
            clr.AddReference(name)
        except Exception:
            pass
    from System import Uri
    from System.Windows.Forms import WebBrowser, WebBrowserNavigatingEventArgs
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
    from unibom.ui import selection

    class FakeOutput(object):
        renderer = WebBrowser()

    before = list(uidoc.Selection.GetElementIds())
    ids = [e.Id.Value if hasattr(e.Id, "Value") else e.Id.IntegerValue
           for e in FilteredElementCollector(doc).OfCategory(
               BuiltInCategory.OST_StructuralFraming).WhereElementIsNotElementType()][:3000]
    links = selection.SelectionLinks(FakeOutput(), uidoc)
    log("hooked", links.hooked)
    html = links.link(ids, u"Select {}".format(len(ids)))
    url = html.split('href="')[1].split('"')[0]
    log("link url", url, "| html chars", len(html))

    args = WebBrowserNavigatingEventArgs(Uri(url), "")
    links._on_navigate(None, args)
    log("navigation cancelled", args.Cancel)
    log("selected", uidoc.Selection.GetElementIds().Count, "of", len(ids))

    from System.Collections.Generic import List
    from Autodesk.Revit.DB import ElementId
    restore = List[ElementId]()
    for i in before:
        restore.Add(i)
    uidoc.Selection.SetElementIds(restore)
    log("selection restored to", len(before))
    log("OK" if uidoc and args.Cancel else "CHECK")
except Exception:
    log("FAILED")
    log(traceback.format_exc())

RESULT = u"\n".join(RESULT)
