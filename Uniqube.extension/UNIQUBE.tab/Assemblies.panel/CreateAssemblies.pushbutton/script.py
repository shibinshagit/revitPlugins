# -*- coding: utf-8 -*-
"""Rebuild assemblies so panel number = BIMSF_Container = assembly name.

The panel number is the real identifier stored in BIMSF_Container (e.g.
ELB-2001 for walls, FT-FloorPanel2001 for floors). This tool guarantees a
clean 1:1 result: exactly one assembly per panel number, named exactly that,
with BIMSF_Container set to match on both the members and the assembly.

How it works (one run):
1. Determine each framing element's panel number. For elements inside an
   existing assembly the number is read from the assembly's BIMSF_Container
   (reliable); for loose framing it is read from the element's own container.
2. Dissolve all existing panel assemblies (members are kept) so the stale or
   mismatched names from earlier runs are cleared.
3. Group all framing by panel number and create ONE assembly per number,
   named exactly the panel number, writing the number into BIMSF_Container on
   the members and the assembly element.
"""
from pyrevit import revit, DB, forms, script
from System.Collections.Generic import List
import panel_utils as pu

doc = revit.doc
logger = script.get_logger()


def _is_structural_framing(elem):
    cat = elem.Category
    if cat is None:
        return False
    try:
        return cat.BuiltInCategory == DB.BuiltInCategory.OST_StructuralFraming
    except Exception:
        try:
            return cat.Id.IntegerValue == int(
                DB.BuiltInCategory.OST_StructuralFraming
            )
        except Exception:
            return False


def _in_assembly(elem):
    try:
        aid = elem.AssemblyInstanceId
    except Exception:
        return False
    return aid is not None and aid != DB.ElementId.InvalidElementId


def _clean(value):
    if value is None:
        return ""
    return value.lstrip("*").strip()


def _get_container(elem):
    p = elem.LookupParameter(pu.PARAM_NAME)
    if p and p.HasValue:
        return p.AsString() or ""
    return ""


def _set_container(elem, value):
    p = elem.LookupParameter(pu.PARAM_NAME)
    if p and not p.IsReadOnly:
        try:
            if p.AsString() != value:
                p.Set(value)
            return True
        except Exception:
            return False
    return False


def build_groups():
    """Return (groups, dissolve_list).

    groups: {panel_number: [ElementId of framing]}
    dissolve_list: [AssemblyInstance] to disassemble before rebuilding
    """
    groups = {}
    seen = set()
    dissolve_list = []

    assemblies = list(
        DB.FilteredElementCollector(doc)
        .OfClass(DB.AssemblyInstance)
        .ToElements()
    )

    for asm in assemblies:
        member_framing = []
        for mid in asm.GetMemberIds():
            m = doc.GetElement(mid)
            if m is not None and _is_structural_framing(m):
                member_framing.append(m.Id)
        if not member_framing:
            continue

        # Panel number: prefer the assembly's container, else a member's.
        pno = _clean(_get_container(asm))
        if not pno:
            for fid in member_framing:
                pno = _clean(_get_container(doc.GetElement(fid)))
                if pno:
                    break
        if not pno:
            continue

        for fid in member_framing:
            groups.setdefault(pno, []).append(fid)
            seen.add(fid.IntegerValue)
        dissolve_list.append(asm)

    # Loose framing (not in any assembly) keyed by its own container.
    framing = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for el in framing:
        if el.Id.IntegerValue in seen:
            continue
        if _in_assembly(el):
            continue
        pno = _clean(_get_container(el))
        if pno:
            groups.setdefault(pno, []).append(el.Id)
            seen.add(el.Id.IntegerValue)

    return groups, dissolve_list


def main():
    groups, dissolve_list = build_groups()

    if not groups:
        forms.alert(
            "No framing with a '{}' panel number found.".format(pu.PARAM_NAME),
            title="UNIQUBE",
        )
        return

    created = 0
    skipped_single = 0

    with revit.Transaction("UNIQUBE: Create Assemblies"):
        # Dissolve existing panel assemblies (members are kept in place).
        for asm in dissolve_list:
            try:
                asm.Disassemble()
            except Exception as ex:
                logger.debug("Disassemble failed: %s", ex)
        doc.Regenerate()

        naming_cat = DB.ElementId(DB.BuiltInCategory.OST_StructuralFraming)

        for pno in sorted(groups.keys()):
            ids = groups[pno]

            # Set the panel number on every member.
            for eid in ids:
                el = doc.GetElement(eid)
                if el is not None:
                    _set_container(el, pno)

            if len(ids) < 2:
                skipped_single += 1
                continue

            id_list = List[DB.ElementId]()
            for eid in ids:
                id_list.Add(eid)

            try:
                new_asm = DB.AssemblyInstance.Create(doc, id_list, naming_cat)
                doc.Regenerate()
                try:
                    new_asm.AssemblyTypeName = pno
                except Exception as ex:
                    logger.debug("Rename failed for %s: %s", pno, ex)
                _set_container(new_asm, pno)
                created += 1
            except Exception as ex:
                logger.debug("Create failed for %s: %s", pno, ex)

    msg = (
        "Done.\n\n"
        "Assemblies rebuilt (1 per panel number): {}\n"
        "Panel number = BIMSF_Container = assembly name.".format(created)
    )
    if skipped_single:
        msg += "\n\nPanels with only one element (no assembly): {}".format(
            skipped_single
        )
    forms.alert(msg, title="UNIQUBE — Create Assemblies")


main()
