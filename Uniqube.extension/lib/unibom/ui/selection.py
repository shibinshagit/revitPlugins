# -*- coding: utf-8 -*-
"""Select-in-Revit links for the UNIBOM report, for any number of elements.

pyRevit 6.5.x puts element ids into the link URL and refuses links longer than
2000 characters (about 117 elements). UNIBOM links instead carry a short key:

    revit://outputhelpers?command=select&unibom=g12&show=false

pyRevit's own handler receives every revit:// link, cancels the navigation and
ignores this one (no element[] in it). Our handler, attached to the same
output window, looks the key up and selects the elements, exactly as pyRevit's
SelectElements does (Selection.SetElementIds, ShowElements for the zoom icon).

If the window cannot be hooked, links fall back to pyRevit's compact
comma-separated form (element[]=1,2,3), which fits about 250 elements.
"""
from __future__ import print_function

import re

FALLBACK_MAX_URL = 1900
_KEY = re.compile(r"[?&]unibom=([A-Za-z0-9_]+)")
_SHOW = re.compile(r"[?&]show=true")

# Keep handlers alive for as long as their output windows live.
_ALIVE = []

ZOOM_ICON = (u'<svg width="11" height="11" viewBox="2 2 20 20" style="vertical-align:-1px">'
             u'<path fill="currentColor" d="M9.5,3A6.5,6.5 0 0,1 16,9.5C16,11.11 15.41,12.59 14.44,13.73L14.71,'
             u'14H15.5L20.5,19L19,20.5L14,15.5V14.71L13.73,14.44C12.59,15.41 11.11,16 9.5,16A6.5,6.5 0 0,1 '
             u'3,9.5A6.5,6.5 0 0,1 9.5,3M9.5,5C7,5 5,7 5,9.5C5,12 7,14 9.5,14C12,14 14,12 14,9.5C14,7 12,5 9.5,5Z"/>'
             u"</svg>")


class SelectionLinks(object):
    """Builds Select / zoom links and handles their clicks."""

    def __init__(self, output, uidoc):
        self.uidoc = uidoc
        self.groups = {}
        self.hooked = False
        try:
            from System.Windows.Forms import WebBrowserNavigatingEventHandler
            renderer = output.renderer
            self._handler = WebBrowserNavigatingEventHandler(self._on_navigate)
            renderer.Navigating += self._handler
            self.hooked = True
            _ALIVE.append(self)
        except Exception:
            self.hooked = False

    # --- links --------------------------------------------------------------
    def link(self, ids, label):
        if not ids:
            return u""
        if self.hooked:
            key = "g{}".format(len(self.groups) + 1)
            self.groups[key] = list(ids)
            base = u"revit://outputhelpers?command=select&unibom={}".format(key)
        else:
            base = u"revit://outputhelpers?command=select&element[]={}".format(
                u",".join(str(i) for i in ids))
            if len(base) > FALLBACK_MAX_URL:
                return u'<span class="dim">{:,} el.</span>'.format(len(ids))
        return (u'<a class="ub-sel" href="{0}&show=false" title="Select in Revit">{1}</a>'
                u'<a class="ub-zoom" href="{0}&show=true" title="Select and zoom to them">{2}</a>').format(
                    base, label, ZOOM_ICON)

    # --- clicks -------------------------------------------------------------
    def _on_navigate(self, sender, args):
        try:
            url = args.Url.ToString()
        except Exception:
            return
        match = _KEY.search(url)
        if not match:
            return
        args.Cancel = True
        ids = self.groups.get(match.group(1))
        if ids:
            self.select(ids, bool(_SHOW.search(url)))

    def select(self, ids, show=False):
        from System.Collections.Generic import List
        from Autodesk.Revit.DB import ElementId

        doc = self.uidoc.Document
        valid = List[ElementId]()
        for value in ids:
            element_id = ElementId(value)
            if doc.GetElement(element_id) is not None:
                valid.Add(element_id)
        if valid.Count == 0:
            return
        if show:
            try:
                self.uidoc.ShowElements(valid)
            except Exception:
                pass
        self.uidoc.Selection.SetElementIds(valid)
