# -*- coding: utf-8 -*-
"""Keep panel + MEP selected together when clicking in the view.

Uses UIApplication.Idling (works on all Revit versions — UIDocument
does not expose SelectionChanged in older releases).
"""
from System import EventHandler
from Autodesk.Revit.UI.Events import IdlingEventArgs
from pyrevit import DB

import panel_utils as pu

_uiapp = None
_idling_handler = None
_host_doc = None
_enabled = False
_syncing = False
_last_fingerprint = None


def _selection_fingerprint(uidoc):
    parts = []
    try:
        for eid in uidoc.Selection.GetElementIds():
            parts.append("H{}".format(eid.IntegerValue))
    except Exception:
        pass
    try:
        for ref in uidoc.Selection.GetReferences():
            le = ref.LinkedElementId
            le_id = le.IntegerValue if le else -1
            parts.append("L{}:{}".format(ref.ElementId.IntegerValue, le_id))
    except Exception:
        pass
    parts.sort()
    return tuple(parts)


def _container_value(elem):
    if elem is None:
        return None
    p = elem.LookupParameter(pu.PARAM_NAME)
    if p and p.HasValue:
        val = p.AsString()
        if val:
            return val
    p = elem.LookupParameter(pu.PANEL_NAME_PARAM)
    if p and p.HasValue:
        val = p.AsString()
        if val:
            return val
    return None


def _host_group_name_for_element(host_doc, elem):
    try:
        gid = elem.GroupId
        if gid is None or gid == DB.ElementId.InvalidElementId:
            return None
        grp = host_doc.GetElement(gid)
        if grp is not None and isinstance(grp, DB.Group):
            return grp.Name
    except Exception:
        pass
    return None


def detect_panel_from_selection(uidoc, host_doc):
    """Return one panel id if the current selection belongs to a single panel."""
    panels = []

    for eid in uidoc.Selection.GetElementIds():
        el = host_doc.GetElement(eid)
        if el is None:
            continue
        if isinstance(el, DB.Group):
            panels.append(pu.panel_display_name(el.Name))
            continue
        c = _container_value(el)
        if c:
            panels.append(c)
            continue
        gname = _host_group_name_for_element(host_doc, el)
        if gname:
            panels.append(pu.panel_display_name(gname))

    invalid = DB.ElementId.InvalidElementId
    for ref in uidoc.Selection.GetReferences():
        try:
            link_elem_id = ref.LinkedElementId
        except Exception:
            link_elem_id = invalid
        if link_elem_id is None or link_elem_id == invalid:
            continue
        link_inst = host_doc.GetElement(ref.ElementId)
        if link_inst is None:
            continue
        link_doc = link_inst.GetLinkDocument()
        if link_doc is None:
            continue
        elem = link_doc.GetElement(link_elem_id)
        if elem is None:
            continue
        if isinstance(elem, DB.Group):
            panels.append(pu.panel_display_name(elem.Name))
            continue
        c = _container_value(elem)
        if c:
            panels.append(c)

    if not panels:
        return None

    displays = set()
    canonical = []
    for p in panels:
        d = pu.panel_display_name(p).lower()
        if d not in displays:
            displays.add(d)
            canonical.append(p)

    if len(displays) == 1:
        return canonical[0]
    return None


def _process_selection(uidoc):
    global _syncing, _last_fingerprint
    if _syncing or not _enabled:
        return

    doc = uidoc.Document
    if _host_doc is not None and doc.Title != _host_doc.Title:
        return

    fp = _selection_fingerprint(uidoc)
    if fp == _last_fingerprint:
        return

    pid = detect_panel_from_selection(uidoc, doc)
    if not pid:
        _last_fingerprint = fp
        return

    try:
        _syncing = True
        link_framing = pu.map_link_framing_by_container(doc)
        pu.select_panel_pair(uidoc, doc, pid, link_framing)
        _last_fingerprint = _selection_fingerprint(uidoc)
    except Exception:
        _last_fingerprint = fp
    finally:
        _syncing = False


def _on_idling(sender, args):
    if not _enabled:
        return
    uidoc = sender.ActiveUIDocument
    if uidoc is None:
        return
    _process_selection(uidoc)


def is_enabled():
    return _enabled


def enable(uidoc):
    global _uiapp, _idling_handler, _host_doc, _enabled, _last_fingerprint
    disable(uidoc)
    _host_doc = uidoc.Document
    _uiapp = uidoc.Application
    _idling_handler = EventHandler[IdlingEventArgs](_on_idling)
    _uiapp.Idling += _idling_handler
    _enabled = True
    _last_fingerprint = None
    return True


def disable(uidoc):
    global _uiapp, _idling_handler, _host_doc, _enabled, _last_fingerprint
    if _idling_handler is not None and _uiapp is not None:
        try:
            _uiapp.Idling -= _idling_handler
        except Exception:
            pass
    _idling_handler = None
    _uiapp = None
    _host_doc = None
    _enabled = False
    _last_fingerprint = None


def toggle(uidoc):
    if _enabled:
        disable(uidoc)
        return False
    enable(uidoc)
    return True
