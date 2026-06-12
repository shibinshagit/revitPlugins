# -*- coding: utf-8 -*-
"""Make panel number = BIMSF_Container = assembly name for every panel.

The panel number is the real identifier already stored in BIMSF_Container
(e.g. ELB-2001 for walls, FT-FloorPanel2001 for floors). This tool makes the
assembly name and BIMSF_Container match that panel number everywhere. It
never uses the auto truss mark (TB-6, CT003-3) as the name.

Two actions in one run:
1. Existing assemblies (e.g. trusses): read the panel number from the
   assembly's BIMSF_Container, write it back (asterisk stripped) and rename
   the assembly to the same value.
2. Wall panels: framing carrying BIMSF_Container directly but not yet in an
   assembly is grouped into a new assembly named after the panel number, with
   BIMSF_Container set on the members and the assembly element.
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
    """Panel number from a raw container value (strip leading '*')."""
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


def _assembly_panel_number(asm):
    """Panel number for an assembly: its own BIMSF_Container, else a member's."""
    pno = _clean(_get_container(asm))
    if pno:
        return pno
    for mid in asm.GetMemberIds():
        m = doc.GetElement(mid)
        if m is None or not _is_structural_framing(m):
            continue
        pno = _clean(_get_container(m))
        if pno:
            return pno
    return ""


def collect_wall_panels():
    """{panel_number: [members]} for framing with a direct container, not yet
    inside any assembly."""
    wall_panels = {}
    framing = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for el in framing:
        if _in_assembly(el):
            continue
        pno = _clean(_get_container(el))
        if pno:
            wall_panels.setdefault(pno, []).append(el)
    return wall_panels


def _rename_assembly(asm, panel_no):
    """Set the assembly type name to the panel number (Revit dedups clashes)."""
    try:
        if asm.AssemblyTypeName != panel_no:
            asm.AssemblyTypeName = panel_no
        return True
    except Exception as ex:
        logger.debug("Rename clash for %s: %s", panel_no, ex)
        return False


def main():
    wall_panels = collect_wall_panels()

    # Existing assemblies (trusses etc.) BEFORE we create new wall ones.
    existing_assemblies = list(
        DB.FilteredElementCollector(doc)
        .OfClass(DB.AssemblyInstance)
        .ToElements()
    )

    if not wall_panels and not existing_assemblies:
        forms.alert(
            "No wall panels with '{}' and no existing assemblies found.".format(
                pu.PARAM_NAME
            ),
            title="UNIQUBE",
        )
        return

    asm_renamed = 0
    asm_no_number = 0
    asm_created = 0

    with revit.Transaction("UNIQUBE: Create Assemblies"):
        # --- 1. Existing assemblies: name + container = panel number ---
        for asm in existing_assemblies:
            panel_no = _assembly_panel_number(asm)
            if not panel_no:
                asm_no_number += 1
                continue
            _set_container(asm, panel_no)
            for mid in asm.GetMemberIds():
                m = doc.GetElement(mid)
                if m is not None and _is_structural_framing(m):
                    _set_container(m, panel_no)
            if _rename_assembly(asm, panel_no):
                asm_renamed += 1

        # --- 2. Wall panels: create assembly named = panel number ---
        for panel_no in sorted(wall_panels.keys()):
            members = wall_panels[panel_no]
            if len(members) < 2:
                continue

            for el in members:
                _set_container(el, panel_no)

            # Remove any existing assembly with the same name (re-run safe).
            for a in list(
                DB.FilteredElementCollector(doc)
                .OfClass(DB.AssemblyInstance)
                .ToElements()
            ):
                try:
                    if a.AssemblyTypeName == panel_no:
                        doc.Delete(a.Id)
                except Exception:
                    pass

            ids = List[DB.ElementId]()
            for el in members:
                ids.Add(el.Id)

            try:
                naming_cat = DB.ElementId(
                    DB.BuiltInCategory.OST_StructuralFraming
                )
                new_asm = DB.AssemblyInstance.Create(doc, ids, naming_cat)
                doc.Regenerate()
                _rename_assembly(new_asm, panel_no)
                _set_container(new_asm, panel_no)
                asm_created += 1
            except Exception as ex:
                logger.debug("Assembly create failed for %s: %s", panel_no, ex)

    msg = (
        "Done.\n\n"
        "Existing assemblies renamed to panel number: {}\n"
        "Wall panel assemblies created: {}".format(asm_renamed, asm_created)
    )
    if asm_no_number:
        msg += (
            "\n\nAssemblies skipped (no BIMSF_Container / panel number): {}\n"
            "Those have no panel number to use. Assign BIMSF_Container "
            "first.".format(asm_no_number)
        )
    forms.alert(msg, title="UNIQUBE — Create Assemblies")


main()
