# -*- coding: utf-8 -*-
"""Keep panel + MEP selected together when clicking in the view.

State is stored on UIApplication so toggle survives pyRevit script reloads
(each ribbon click re-imports this module with fresh module-level globals).
"""
from System import EventHandler
from Autodesk.Revit.UI.Events import IdlingEventArgs
from pyrevit import DB

import panel_utils as pu

_STATE_KEY = "_uniqube_panel_sync_state"


def _state(uiapp):
    """Shared sync state bag on UIApplication (persists across script runs)."""
    st = getattr(uiapp, _STATE_KEY, None)
    if st is None:
        st = {
            "enabled": False,
            "handler": None,
            "host_doc_title": None,
            "syncing": False,
            "last_fingerprint": None,
            "generation": 0,
        }
        setattr(uiapp, _STATE_KEY, st)
    return st


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


def _make_idling_handler(st, my_generation):
    def _on_idling(sender, args):
        if not st.get("enabled") or st.get("generation") != my_generation:
            return
        if st.get("syncing"):
            return
        uidoc = sender.ActiveUIDocument
        if uidoc is None:
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

    return EventHandler[IdlingEventArgs](_on_idling)


def is_enabled(uidoc):
    """True when panel selection sync is active for this Revit session."""
    if uidoc is None:
        return False
    return bool(_state(uidoc.Application).get("enabled"))


def enable(uidoc):
    """Turn panel selection sync ON."""
    uiapp = uidoc.Application
    disable(uidoc)
    st = _state(uiapp)
    st["generation"] = st.get("generation", 0) + 1
    my_generation = st["generation"]
    handler = _make_idling_handler(st, my_generation)
    uiapp.Idling += handler
    st["handler"] = handler
    st["enabled"] = True
    st["host_doc_title"] = uidoc.Document.Title
    st["last_fingerprint"] = None
    st["syncing"] = False
    return True


def disable(uidoc):
    """Turn panel selection sync OFF."""
    if uidoc is None:
        return False
    uiapp = uidoc.Application
    st = _state(uiapp)
    st["enabled"] = False
    st["generation"] = st.get("generation", 0) + 1
    st["syncing"] = False
    st["last_fingerprint"] = None
    handler = st.get("handler")
    if handler is not None:
        try:
            uiapp.Idling -= handler
        except Exception:
            pass
    st["handler"] = None
    st["host_doc_title"] = None
    return False


def toggle(uidoc):
    if is_enabled(uidoc):
        disable(uidoc)
        return False
    enable(uidoc)
    return True
