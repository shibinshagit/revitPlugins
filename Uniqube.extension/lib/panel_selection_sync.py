# -*- coding: utf-8 -*-
"""Keep panel + MEP selected together when clicking in the view.

Sync ON/OFF is stored with pyRevit script env vars (shared across all ribbon
buttons). select_panel_pair() checks the same flag so auto-selection stops
when sync is OFF.

After Prepare MEP Panels copies framing into the host, elements live in Revit
groups — clicking one member selects the whole group even with sync OFF.
That is normal Revit behaviour; press Tab to pick a single element.
"""
from System import EventHandler
from Autodesk.Revit.UI.Events import IdlingEventArgs
from pyrevit import DB, script

import panel_utils as pu

_ENV_ACTIVE = "uniqube_panel_sync_active"
_SESSIONS_KEY = "uniqube_panel_sync_sessions"

# Module-level handler ref (lib module stays loaded between script runs).
_idling_handler = None
_idling_uiapp_id = None


def _sessions():
    if _SESSIONS_KEY not in globals():
        globals()[_SESSIONS_KEY] = {}
    return globals()[_SESSIONS_KEY]


def _uiapp_key(uiapp):
    return uiapp.GetHashCode()


def _state(uiapp):
    sessions = _sessions()
    key = _uiapp_key(uiapp)
    if key not in sessions:
        sessions[key] = {
            "host_doc_title": None,
            "syncing": False,
            "last_fingerprint": None,
        }
    return sessions[key]


def _set_active(value):
    script.set_envvar(_ENV_ACTIVE, bool(value))


def is_enabled(uidoc=None):
    """True when panel selection sync is active for this Revit session."""
    val = script.get_envvar(_ENV_ACTIVE)
    if val is None:
        return False
    return bool(val)


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
    if not is_enabled(uidoc):
        return
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


def _on_idling(sender, args):
    if not is_enabled():
        return
    uidoc = sender.ActiveUIDocument
    if uidoc is None:
        return
    st = _state(sender)
    _process_selection(uidoc, st)


def _detach_handler(uiapp):
    global _idling_handler, _idling_uiapp_id
    if _idling_handler is not None:
        try:
            uiapp.Idling -= _idling_handler
        except Exception:
            pass
    _idling_handler = None
    _idling_uiapp_id = None


def _attach_handler(uiapp):
    global _idling_handler, _idling_uiapp_id
    key = _uiapp_key(uiapp)
    if _idling_handler is not None and _idling_uiapp_id == key:
        return
    _detach_handler(uiapp)
    _idling_handler = EventHandler[IdlingEventArgs](_on_idling)
    uiapp.Idling += _idling_handler
    _idling_uiapp_id = key


def enable(uidoc):
    """Turn panel selection sync ON."""
    uiapp = uidoc.Application
    st = _state(uiapp)
    st["host_doc_title"] = uidoc.Document.Title
    st["last_fingerprint"] = None
    st["syncing"] = False
    _attach_handler(uiapp)
    _set_active(True)
    return True


def disable(uidoc):
    """Turn panel selection sync OFF and detach the Idling handler."""
    if uidoc is not None:
        uiapp = uidoc.Application
        st = _state(uiapp)
        st["syncing"] = False
        st["last_fingerprint"] = None
        st["host_doc_title"] = None
        _detach_handler(uiapp)
    _set_active(False)
    return False


def toggle(uidoc):
    if is_enabled(uidoc):
        disable(uidoc)
        return False
    enable(uidoc)
    return True
