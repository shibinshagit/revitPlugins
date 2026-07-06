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
_last_panel_key = None
_link_framing_cache = None
_link_framing_doc_title = None


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


def _panel_key(pid):
    if not pid:
        return None
    return pu.panel_display_name(pid).lower()


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
        aid = elem.AssemblyInstanceId
        if aid is not None and aid != DB.ElementId.InvalidElementId:
            asm = host_doc.GetElement(aid)
            if asm is not None and isinstance(asm, DB.AssemblyInstance):
                c = _container_value(asm)
                if c:
                    return c
                return asm.AssemblyTypeName
    except Exception:
        pass
    try:
        gid = elem.GroupId
        if gid is None or gid == DB.ElementId.InvalidElementId:
            return None
        grp = host_doc.GetElement(gid)
        if grp is not None and isinstance(grp, DB.Group):
            return pu.group_label(grp)
    except Exception:
        pass
    return None


def _host_container_for_panel(host_doc, pid):
    asm = pu.host_assembly_for_panel(host_doc, pid)
    if asm is not None:
        return asm
    for g in (
        DB.FilteredElementCollector(host_doc)
        .OfClass(DB.Group)
        .ToElements()
    ):
        if pu.group_matches_panel(pu.group_label(g), pid):
            return g
    return None


def _selection_includes_container(uidoc, container):
    if container is None:
        return False
    cid = container.Id.IntegerValue
    try:
        for eid in uidoc.Selection.GetElementIds():
            if eid.IntegerValue == cid:
                return True
    except Exception:
        pass
    try:
        for ref in uidoc.Selection.GetReferences():
            if ref.LinkedElementId != DB.ElementId.InvalidElementId:
                continue
            if ref.ElementId.IntegerValue == cid:
                return True
    except Exception:
        pass
    return False


def _assembly_fully_selected(uidoc, asm):
    """True when every assembly member is already in the selection."""
    if asm is None:
        return False
    try:
        if asm.Id.IntegerValue in {
            eid.IntegerValue for eid in uidoc.Selection.GetElementIds()
        }:
            return True
    except Exception:
        pass
    try:
        required = {
            mid.IntegerValue for mid in asm.GetMemberIds()
        }
    except Exception:
        return False
    if not required:
        return False
    try:
        selected = {
            eid.IntegerValue for eid in uidoc.Selection.GetElementIds()
        }
    except Exception:
        return False
    return required.issubset(selected) and len(selected) == len(required)


def _panel_fully_selected(uidoc, host_doc, pid):
    container = _host_container_for_panel(host_doc, pid)
    if isinstance(container, DB.AssemblyInstance):
        if _selection_includes_container(uidoc, container):
            return True
        try:
            selected = {
                eid.IntegerValue for eid in uidoc.Selection.GetElementIds()
            }
            member_ids = {
                mid.IntegerValue for mid in container.GetMemberIds()
            }
            if member_ids and selected == member_ids:
                return True
        except Exception:
            pass
        return False
    if isinstance(container, DB.Group):
        return _selection_includes_container(uidoc, container)
    required = pu._host_panel_member_ids(host_doc, pid)
    if required:
        try:
            selected = {
                eid.IntegerValue for eid in uidoc.Selection.GetElementIds()
            }
        except Exception:
            selected = set()
        if required.issubset(selected) and len(selected) == len(required):
            return True
    return _selection_includes_container(uidoc, container)


def detect_panel_from_selection(uidoc, host_doc):
    """Return one panel id if the current selection belongs to a single panel."""
    panels = []

    for eid in uidoc.Selection.GetElementIds():
        el = host_doc.GetElement(eid)
        if el is None:
            continue
        if isinstance(el, DB.AssemblyInstance):
            c = _container_value(el)
            if c:
                panels.append(c)
            else:
                panels.append(pu.panel_display_name(el.AssemblyTypeName))
            continue
        try:
            aid = el.AssemblyInstanceId
            if aid is not None and aid != DB.ElementId.InvalidElementId:
                asm = host_doc.GetElement(aid)
                if asm is not None and isinstance(asm, DB.AssemblyInstance):
                    c = _container_value(asm)
                    if c:
                        panels.append(c)
                    else:
                        panels.append(
                            pu.panel_display_name(asm.AssemblyTypeName)
                        )
                    continue
        except Exception:
            pass
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
        if isinstance(elem, DB.AssemblyInstance):
            panels.append(pu.panel_display_name(elem.AssemblyTypeName))
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


def _cached_link_framing(doc):
    global _link_framing_cache, _link_framing_doc_title
    title = doc.Title
    if _link_framing_cache is None or _link_framing_doc_title != title:
        _link_framing_cache = pu.map_link_framing_by_container(doc)
        _link_framing_doc_title = title
    return _link_framing_cache


def _process_selection(uidoc):
    global _syncing, _last_panel_key
    if _syncing or not _enabled:
        return

    doc = uidoc.Document
    if _host_doc is not None and doc.Title != _host_doc.Title:
        return

    fp = _selection_fingerprint(uidoc)
    if not fp:
        _last_panel_key = None
        return

    pid = detect_panel_from_selection(uidoc, doc)
    if not pid:
        _last_panel_key = None
        return

    panel_key = _panel_key(pid)
    if _panel_fully_selected(uidoc, doc, pid):
        _last_panel_key = panel_key
        return

    try:
        _syncing = True
        link_framing = _cached_link_framing(doc)
        pu.select_panel_pair(uidoc, doc, pid, link_framing)
        _last_panel_key = panel_key
    except Exception:
        _last_panel_key = panel_key
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
    global _uiapp, _idling_handler, _host_doc, _enabled, _last_panel_key
    global _link_framing_cache, _link_framing_doc_title
    disable(uidoc)
    _host_doc = uidoc.Document
    _uiapp = uidoc.Application
    _idling_handler = EventHandler[IdlingEventArgs](_on_idling)
    _uiapp.Idling += _idling_handler
    _enabled = True
    _last_panel_key = None
    _link_framing_cache = None
    _link_framing_doc_title = None
    return True


def disable(uidoc):
    global _uiapp, _idling_handler, _host_doc, _enabled, _last_panel_key
    global _link_framing_cache, _link_framing_doc_title
    if _idling_handler is not None and _uiapp is not None:
        try:
            _uiapp.Idling -= _idling_handler
        except Exception:
            pass
    _idling_handler = None
    _uiapp = None
    _host_doc = None
    _enabled = False
    _last_panel_key = None
    _link_framing_cache = None
    _link_framing_doc_title = None


def toggle(uidoc):
    if _enabled:
        disable(uidoc)
        return False
    enable(uidoc)
    return True
