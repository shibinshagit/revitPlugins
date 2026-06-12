# -*- coding: utf-8 -*-
"""Create assemblies for panels and keep panel number = container = assembly.

Actions in one run:
1. Wall panels are auto-ungrouped first, because Revit cannot place grouped
   elements into an assembly.
2. Wall panels: structural framing carrying BIMSF_Container directly (and not
   already inside an assembly) are grouped into a fresh assembly per panel.
   The panel number, BIMSF_Container, and assembly name are all set to the
   same value (asterisk stripped, e.g. *ELB-2001 -> ELB-2001).
3. Trusses: members already live inside an assembly (e.g. TB-4, CT003-3). The
   assembly name is written into each member's BIMSF_Container, so the
   container matches the assembly name (e.g. *FT-FloorPanel2001 -> TB-4).
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


def _asm_name(value):
    """Wall assembly name from a container value (strip leading '*')."""
    if value is None:
        return ""
    return value.lstrip("*")


def collect_wall_panels():
    """{container: [members]} for framing with a direct container, not yet
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
        p = el.LookupParameter(pu.PARAM_NAME)
        if p and p.HasValue and p.AsString():
            wall_panels.setdefault(p.AsString(), []).append(el)
    return wall_panels


def main():
    wall_panels = collect_wall_panels()

    # Snapshot existing assemblies BEFORE we create new ones (these are trusses).
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

    asm_created = 0
    truss_updated = 0
    truss_members_set = 0
    groups_dissolved = 0
    name_results = []   # (target_name, actual_name)
    errors = []

    with revit.Transaction("UNIQUBE: Create Assemblies"):
        # --- 0. Ungroup wall members: Revit cannot put grouped elements into
        #        an assembly, so dissolve any group holding a wall member. ---
        group_ids = set()
        for members in wall_panels.values():
            for el in members:
                try:
                    gid = el.GroupId
                except Exception:
                    gid = None
                if gid and gid != DB.ElementId.InvalidElementId:
                    group_ids.add(gid.IntegerValue)
        for gid_int in group_ids:
            g = doc.GetElement(DB.ElementId(gid_int))
            if g is None:
                continue
            try:
                g.UngroupMembers()
                groups_dissolved += 1
            except Exception as ex:
                errors.append("Ungroup: {}".format(ex))
        if group_ids:
            doc.Regenerate()

        # --- 1. Trusses: write assembly name into members' BIMSF_Container ---
        for asm in existing_assemblies:
            try:
                name = asm.AssemblyTypeName
            except Exception:
                name = None
            if not name:
                continue
            member_changed = False
            for mid in asm.GetMemberIds():
                member = doc.GetElement(mid)
                if member is None or not _is_structural_framing(member):
                    continue
                p = member.LookupParameter(pu.PARAM_NAME)
                if p and not p.IsReadOnly:
                    try:
                        if p.AsString() != name:
                            p.Set(name)
                            truss_members_set += 1
                            member_changed = True
                    except Exception:
                        pass
            if member_changed:
                truss_updated += 1

        # --- 2. Wall panels: unify panel number = BIMSF_Container = assembly ---
        for container in sorted(wall_panels.keys()):
            members = wall_panels[container]
            if len(members) < 2:
                continue  # an assembly needs more than one element

            # Panel number drives all three names (asterisk stripped).
            target_name = _asm_name(container)

            # Write the clean panel number back into BIMSF_Container so the
            # container value matches the panel number and assembly name.
            for el in members:
                p = el.LookupParameter(pu.PARAM_NAME)
                if p and not p.IsReadOnly:
                    try:
                        if p.AsString() != target_name:
                            p.Set(target_name)
                    except Exception:
                        pass

            # Remove any existing assembly with the same target name (re-run).
            for a in list(
                DB.FilteredElementCollector(doc)
                .OfClass(DB.AssemblyInstance)
                .ToElements()
            ):
                try:
                    if a.AssemblyTypeName == target_name:
                        doc.Delete(a.Id)
                except Exception:
                    pass
            doc.Regenerate()

            ids = List[DB.ElementId]()
            for el in members:
                ids.Add(el.Id)

            try:
                naming_cat = DB.ElementId(
                    DB.BuiltInCategory.OST_StructuralFraming
                )
                new_asm = DB.AssemblyInstance.Create(doc, ids, naming_cat)
                doc.Regenerate()
                asm_created += 1

                # Rename to the panel number. Retry once after a regenerate;
                # capture the real error so we can see why it would fail.
                set_ok = _try_set_name(new_asm, target_name, errors)
                if not set_ok:
                    doc.Regenerate()
                    set_ok = _try_set_name(new_asm, target_name, errors)

                try:
                    actual = new_asm.AssemblyTypeName
                except Exception:
                    actual = "?"
                name_results.append((target_name, actual))
            except Exception as ex:
                errors.append("Create '{}': {}".format(target_name, ex))

    # Build report
    mismatches = [
        "  {} -> got '{}'".format(t, a)
        for (t, a) in name_results if a != t
    ]
    msg = (
        "Done.\n\n"
        "Wall panel assemblies created: {}\n"
        "Groups dissolved (to allow assembly): {}\n"
        "Truss assemblies updated: {}\n"
        "Truss members re-tagged: {}".format(
            asm_created, groups_dissolved, truss_updated, truss_members_set
        )
    )
    if mismatches:
        msg += "\n\nNames that did NOT match the panel number:\n" + "\n".join(
            mismatches[:10]
        )
    if errors:
        msg += "\n\nErrors:\n" + "\n".join(errors[:5])

    forms.alert(msg, title="UNIQUBE — Create Assemblies")


def _try_set_name(asm, target_name, errors):
    """Set AssemblyTypeName, recording any exception. Returns True on success."""
    try:
        asm.AssemblyTypeName = target_name
        return True
    except Exception as ex:
        errors.append("Rename to '{}': {}".format(target_name, ex))
        return False


main()
