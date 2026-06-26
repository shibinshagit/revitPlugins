# -*- coding: utf-8 -*-
"""Keep panel + MEP selected together when clicking in the view.

Session state lives in IronPython __main__ (Revit UIApplication cannot hold
custom Python attributes). One Idling handler per session; enable/disable
only flips a flag so OFF immediately stops auto-selection.
"""
import __main__

from System import EventHandler
from Autodesk.Revit.UI.Events import IdlingEventArgs
from pyrevit import DB

import panel_utils as pu

_SESSIONS_KEY = "_uniqube_panel_sync_sessions"
_HANDLERS_KEY = "_uniqube_panel_sync_idling_handlers"


def _sessions():
    if not hasattr(__main__, _SESSIONS_KEY):
        setattr(__main__, _SESSIONS_KEY, {})
    return getattr(__main__, _SESSIONS_KEY)


def _handlers():
    if not hasattr(__main__, _HANDLERS_KEY):
        setattr(__main__, _HANDLERS_KEY, {})
    return getattr(__main__, _HANDLERS_KEY)


def _uiapp_key(uiapp):
    return uiapp.GetHashCode()


def _state(uiapp):
    sessions = _sessions()
    key = _uiapp_key(uiapp)
    if key not in sessions:
        sessions[key] = {
            "enabled": False,
            "host_doc_title": None,
            "syncing": False,
            "last_fingerprint": None,
        }
    return sessions[key]


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


def _process_selection(uidoc, st):
    if st.get("syncing"):
        return
    doc = uidoc.Document
    host_title = st.get("host_doc_title")
    if host_title and doc.Title != host_title:
        return

    fp = _selection_fingerprint(uidoc)
    if fp == st.get("last_fingerprint"):
        return

    pid = detect_panel_from_selection(uidoc, doc)
    if not pid:
        st["last_fingerprint"] = fp
        return

    try:
        st["syncing"] = True
        link_framing = pu.map_link_framing_by_container(doc)
        pu.select_panel_pair(uidoc, doc, pid, link_framing)
        st["last_fingerprint"] = _selection_fingerprint(uidoc)
    except Exception:
        st["last_fingerprint"] = fp
    finally:
        st["syncing"] = False


def _ensure_idling_handler(uiapp):
    """Attach exactly one Idling handler per UIApplication per Revit session."""
    handlers = _handlers()
    key = _uiapp_key(uiapp)
    if key in handlers:
        return

    st = _state(uiapp)

    def _on_idling(sender, args):
        if not st.get("enabled"):
            return
        uidoc = sender.ActiveUIDocument
        if uidoc is None:
            return
        _process_selection(uidoc, st)

    handler = EventHandler[IdlingEventArgs](_on_idling)
    uiapp.Idling += handler
    handlers[key] = handler


def is_enabled(uidoc):
    """True when panel selection sync is active for this Revit session."""
    if uidoc is None:
        return False
    try:
        uiapp = uidoc.Application
        key = _uiapp_key(uiapp)
        sessions = _sessions()
        if key not in sessions:
            return False
        return bool(sessions[key].get("enabled"))
    except Exception:
        return False


def enable(uidoc):
    """Turn panel selection sync ON."""
    uiapp = uidoc.Application
    _ensure_idling_handler(uiapp)
    st = _state(uiapp)
    st["enabled"] = True
    st["host_doc_title"] = uidoc.Document.Title
    st["last_fingerprint"] = None
    st["syncing"] = False
    return True


def disable(uidoc):
    """Turn panel selection sync OFF (handler stays; flag stops selection)."""
    if uidoc is None:
        return False
    uiapp = uidoc.Application
    st = _state(uiapp)
    st["enabled"] = False
    st["syncing"] = False
    st["last_fingerprint"] = None
    st["host_doc_title"] = None
    return False


def toggle(uidoc):
    if is_enabled(uidoc):
        disable(uidoc)
        return False
    enable(uidoc)
    return True
