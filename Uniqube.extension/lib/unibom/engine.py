# -*- coding: utf-8 -*-
"""UNIBOM engine: records + rules -> BOQ lines and model issues.

Pure Python, no Revit imports, so it runs in pyRevit (IronPython 2.7), in
CPython for tests, and can be ported line for line to the uni-server.

Records are plain dicts produced by unibom.collect (Revit side):

  member  {"id", "category", "family", "type", "role", "label", "container",
           "level", "length_mm", "kg_per_m_revit", "width_mm", "height_mm",
           "thickness_mm", "area_m2", "sheet_w_mm", "sheet_h_mm"}
  wall    {"id", "type", "level", "length_mm", "height_mm", "openings",
           "stud_spacing_mm", "container"}   container set = framed by BIMSF
"""
from __future__ import division

import io
import json
import os
import re
from collections import OrderedDict

from unibom import formula

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
UNITS = ("nr", "m", "m2", "kg")
GROUP_ORDER = ["LGS profile", "LGS strap", "Board", "Openings", "Fixings", "Roofing", "Other"]


def group_key(group, item=""):
    rank = GROUP_ORDER.index(group) if group in GROUP_ORDER else len(GROUP_ORDER)
    return (rank, group or "", item or "")
_GRADE_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z_0-9]*)\}")


# --- library ---------------------------------------------------------------
def _load_json(path):
    with io.open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


class Library(object):
    """Settings, item master and rules, loaded from the data folder."""

    def __init__(self, data_dir=None):
        self.data_dir = data_dir or DATA_DIR
        self.settings = _load_json(os.path.join(self.data_dir, "settings.json"))
        items = _load_json(os.path.join(self.data_dir, "items.json"))
        self.items = OrderedDict((i["code"], i) for i in items["items"])
        self.member_rules = _load_json(
            os.path.join(self.data_dir, "member_rules.json"))["rules"]
        recipes = _load_json(os.path.join(self.data_dir, "wall_recipes.json"))
        self.wall_sections = recipes["wall_sections"]
        self.wall_recipes = recipes["recipes"]
        self.params = dict(
            (k, v["value"]) for k, v in self.settings["parameters"].items())
        self.roles = self.settings.get("roles", {})
        self._systems = [
            (re.compile(s["match"]), s["name"]) for s in self.settings["systems"]]
        self._rules = []
        for rule in self.member_rules:
            compiled = dict(
                (k, re.compile(v)) for k, v in (rule.get("match") or {}).items())
            self._rules.append((rule, compiled))

    def param(self, name):
        return self.params[name]

    def system_of(self, container):
        if not container:
            return self.settings.get("unassigned_system", "Not in a panel")
        for rx, name in self._systems:
            if rx.search(container):
                return name
        return "Other panels"

    def role_name(self, role):
        return self.roles.get(role or "", role or "")

    def match_rule(self, record):
        for rule, compiled in self._rules:
            ok = True
            for key, rx in compiled.items():
                if not rx.search(_text(record.get(key))):
                    ok = False
                    break
            if ok:
                return rule
        return None

    def section_for_wall(self, wall_type):
        for entry in self.wall_sections:
            if re.search(entry["match"], wall_type or ""):
                return entry["section"]
        return None


# --- helpers ---------------------------------------------------------------
def _text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return u"{}".format(value)


def section_of(type_name):
    """600S162-54(50) -> 600S162-54 (drops the steel grade suffix)."""
    return _GRADE_SUFFIX.sub("", type_name or "").strip()


def fill(template, fields):
    """Replace {name} placeholders; returns (text, [missing names])."""
    missing = []

    def repl(m):
        key = m.group(1)
        value = fields.get(key)
        if value in (None, ""):
            missing.append(key)
            return m.group(0)
        return _text(value)

    return _PLACEHOLDER.sub(repl, template or ""), missing


def _num(value):
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


class Issue(object):
    """Something an estimator must look at before trusting the BOQ."""

    def __init__(self, severity, code, message, ids=None):
        self.severity = severity        # "error" | "warning" | "info"
        self.code = code
        self.message = message
        self.ids = list(ids or [])


class Line(object):
    """One BOQ line: an item, in a system, for one role (stud, track, ...)."""

    def __init__(self, system, item, name, role, group):
        self.system = system
        self.item = item
        self.name = name
        self.role = role
        self.group = group
        self.qty = dict((u, 0.0) for u in UNITS)
        self.ids = []

    def add(self, qty, element_id):
        for unit, value in qty.items():
            self.qty[unit] = self.qty.get(unit, 0.0) + value
        if element_id is not None:
            self.ids.append(element_id)


class Takeoff(object):
    def __init__(self, mode, library):
        self.mode = mode                # "counted" | "recipe"
        self.library = library
        self.lines = OrderedDict()
        self.issues = []
        self.ignored = 0
        self.source_count = 0

    def line(self, system, item, role, group=None):
        key = (system, item, role or "")
        if key not in self.lines:
            meta = self.library.items.get(item, {})
            self.lines[key] = Line(
                system, item, meta.get("name") or item, role or "",
                meta.get("group") or group or "Other")
        return self.lines[key]

    # roll-ups ---------------------------------------------------------------
    def by_item(self):
        """One row per item across all systems and roles."""
        out = OrderedDict()
        for line in self.lines.values():
            row = out.get(line.item)
            if row is None:
                row = Line("", line.item, line.name, "", line.group)
                out[line.item] = row
            row.add(line.qty, None)
            row.ids.extend(line.ids)
        return sorted(out.values(), key=lambda r: group_key(r.group, r.item))

    def by_system(self):
        """System -> item rows, roles merged."""
        out = OrderedDict()
        for line in self.lines.values():
            items = out.setdefault(line.system, OrderedDict())
            row = items.get(line.item)
            if row is None:
                row = Line(line.system, line.item, line.name, "", line.group)
                items[line.item] = row
            row.add(line.qty, None)
            row.ids.extend(line.ids)
        return OrderedDict(
            (k, sorted(v.values(), key=lambda r: group_key(r.group, r.item)))
            for k, v in sorted(out.items()))

    def lgs_summary(self):
        """Net LGS kg, wastage, gross supply kg and screws, from settings."""
        net = sum(l.qty["kg"] for l in self.lines.values()
                  if l.group in ("LGS profile", "LGS strap"))
        wastage = self.library.param("lgs_wastage")
        return OrderedDict([
            ("net_kg", net),
            ("wastage", wastage),
            ("gross_kg", net * (1 + wastage)),
            ("screws_nr", net * self.library.param("screws_per_kg")),
        ])

    def board_summary(self):
        """Per board item: net m2, sheets to buy with wastage."""
        out = []
        waste = self.library.param("board_wastage")
        for row in self.by_item():
            meta = self.library.items.get(row.item, {})
            if row.group != "Board" or not meta.get("sheet_w_mm"):
                continue
            sheet_m2 = meta["sheet_w_mm"] * meta["sheet_h_mm"] / 1e6
            gross = row.qty["m2"] * (1 + waste)
            sheets = int(-(-gross // sheet_m2))
            out.append((row.item, row.qty["m2"], gross, sheets))
        return out


# --- counted mode -------------------------------------------------------------
def _member_scope(record, library):
    scope = dict((k, v) for k, v in record.items()
                 if isinstance(v, (int, float)) and not isinstance(v, bool))
    scope["length_m"] = _num(record.get("length_mm")) / 1000.0
    scope["sheet_area_m2"] = (
        _num(record.get("sheet_w_mm")) * _num(record.get("sheet_h_mm")) / 1e6)
    for key in ("width_mm", "height_mm", "area_m2", "thickness_mm"):
        scope.setdefault(key, _num(record.get(key)))
    return scope


def _fields(record):
    fields = dict(record)
    fields["section"] = section_of(record.get("type"))
    return fields


def run_counted(records, library):
    """Counted mode: every modelled part becomes BOQ quantity."""
    takeoff = Takeoff("counted", library)
    takeoff.source_count = len(records)
    no_rule = OrderedDict()
    missing_item = OrderedDict()
    template_gaps = OrderedDict()

    for record in records:
        rule = library.match_rule(record)
        if rule is None:
            key = u"{} : {} : {}".format(
                record.get("category"), record.get("family"), record.get("type"))
            no_rule.setdefault(key, []).append(record["id"])
            continue
        if rule.get("ignore"):
            takeoff.ignored += 1
            continue

        fields = _fields(record)
        item, gaps = fill(rule["item"], fields)
        if gaps:
            template_gaps.setdefault(
                u"{} ({})".format(rule["id"], ", ".join(gaps)), []).append(record["id"])
            continue
        if rule.get("library") and item not in library.items:
            missing_item.setdefault(item, []).append(record["id"])

        scope = _member_scope(record, library)
        meta = library.items.get(item, {})
        scope["kg_per_m"] = _num(meta.get("kg_per_m") or record.get("kg_per_m_revit"))

        qty = {}
        try:
            for unit, expr in rule["qty"].items():
                qty[unit] = formula.evaluate(expr, scope)
        except formula.FormulaError as ex:
            takeoff.issues.append(Issue("error", "FORMULA", u"{}: {}".format(rule["id"], ex), [record["id"]]))
            continue

        system = library.system_of(record.get("container"))
        if rule.get("group") and system in (library.system_of(None), "Other panels"):
            # Openings, fixings, roofing: report under their own heading when
            # they are not part of a BIMSF panel.
            system = rule["group"]
        role = fill(rule.get("role", "{role}"), fields)[0]
        if role.startswith("{"):
            role = ""
        line = takeoff.line(system, item, role, rule.get("group"))
        if item not in library.items and rule.get("name"):
            line.name = fill(rule["name"], fields)[0]
        line.add(qty, record["id"])

    for key, ids in no_rule.items():
        takeoff.issues.append(Issue(
            "warning", "NO_RULE",
            u"{} element(s) match no rule, not in the BOQ: {}".format(len(ids), key), ids))
    for item, ids in missing_item.items():
        takeoff.issues.append(Issue(
            "error", "NOT_IN_LIBRARY",
            u"Item {} is not in items.json ({} element(s)); quantities are counted but kg uses the Revit type weight".format(item, len(ids)), ids))
    for key, ids in template_gaps.items():
        takeoff.issues.append(Issue(
            "error", "MISSING_DATA",
            u"{} element(s) lack data for rule {}".format(len(ids), key), ids))
    return takeoff


# --- recipe mode -----------------------------------------------------------
def run_recipes(walls, library, only_unframed=False):
    """Recipe mode: estimate wall framing from wall length, height, openings."""
    takeoff = Takeoff("recipe", library)
    names = {"P": library.params}
    no_section = OrderedDict()
    for wall in walls:
        if only_unframed and wall.get("container"):
            continue
        takeoff.source_count += 1
        section = library.section_for_wall(wall.get("type"))
        if not section:
            no_section.setdefault(wall.get("type"), []).append(wall["id"])
            continue
        scope = dict(names)
        scope.update({
            "L": _num(wall.get("length_mm")),
            "H": _num(wall.get("height_mm")),
            "openings": _num(wall.get("openings")),
            "stud_spacing_mm": _num(wall.get("stud_spacing_mm")) or library.param("stud_spacing_mm"),
        })
        system = library.system_of(wall.get("container") or wall.get("type"))
        for recipe in library.wall_recipes:
            try:
                local = formula.evaluate_vars(recipe.get("vars"), scope)
                for emit in recipe["emits"]:
                    item = fill(emit["item"], {"section": section})[0]
                    qty = dict((u, formula.evaluate(e, local)) for u, e in emit["qty"].items())
                    if "m" in qty and "kg" not in qty:
                        qty["kg"] = qty["m"] * _num(library.items.get(item, {}).get("kg_per_m"))
                    takeoff.line(system, item, emit.get("role")).add(qty, wall["id"])
            except formula.FormulaError as ex:
                takeoff.issues.append(Issue("error", "FORMULA", u"{}: {}".format(recipe["id"], ex), [wall["id"]]))
    for wall_type, ids in no_section.items():
        takeoff.issues.append(Issue(
            "warning", "NO_WALL_SECTION",
            u"Wall type '{}' has no section in wall_recipes.json ({} wall(s))".format(wall_type, len(ids)), ids))
    takeoff.issues.append(Issue(
        "info", "NO_JUNCTIONS",
        "Recipe mode does not count L/T/X junction studs yet (spec rule studs_junction)."))
    return takeoff


# --- check model -----------------------------------------------------------
def check_model(records, walls, library):
    """Everything that would make the BOQ wrong, before running it."""
    issues = []
    counted = run_counted(records, library)
    issues.extend(i for i in counted.issues)

    tol = library.param("kg_per_m_tolerance")
    weight_gaps = OrderedDict()
    no_panel = []
    zero_len = []
    no_size = []
    for record in records:
        rule = library.match_rule(record)
        if rule is None or rule.get("ignore"):
            continue
        item = fill(rule.get("item", ""), _fields(record))[0]
        meta = library.items.get(item, {})
        revit_kg = _num(record.get("kg_per_m_revit"))
        lib_kg = _num(meta.get("kg_per_m"))
        if lib_kg and revit_kg and abs(revit_kg - lib_kg) / lib_kg > tol:
            weight_gaps.setdefault((item, record.get("type"), revit_kg, lib_kg), []).append(record["id"])
        if rule["id"] in ("LGS-PROFILE", "STRAP", "SHEATHING"):
            if not record.get("container"):
                no_panel.append(record["id"])
            if rule["id"] != "SHEATHING" and _num(record.get("length_mm")) <= 0:
                zero_len.append(record["id"])
        if rule["id"] in ("WINDOW", "DOOR") and (
                _num(record.get("width_mm")) <= 0 or _num(record.get("height_mm")) <= 0):
            no_size.append(record["id"])

    for (item, type_name, revit_kg, lib_kg), ids in weight_gaps.items():
        issues.append(Issue(
            "warning", "WEIGHT_MISMATCH",
            u"{}: Revit type '{}' says {:.2f} kg/m, item master says {:.2f} kg/m ({} element(s)). The BOQ uses the item master.".format(
                item, type_name, revit_kg, lib_kg, len(ids)), ids))
    if no_panel:
        issues.append(Issue(
            "warning", "NO_PANEL",
            u"{} framing element(s) have no BIMSF_Container; they are reported under '{}'.".format(
                len(no_panel), library.system_of(None)), no_panel))
    if zero_len:
        issues.append(Issue("error", "ZERO_LENGTH", u"{} member(s) have zero cut length.".format(len(zero_len)), zero_len))
    if no_size:
        issues.append(Issue("warning", "NO_SIZE", u"{} window(s)/door(s) have no width or height; m2 is 0.".format(len(no_size)), no_size))

    unframed = [w["id"] for w in walls if not w.get("container")]
    if unframed:
        issues.append(Issue(
            "info", "WALL_NOT_FRAMED",
            u"{} wall(s) are not framed by BIMSF; only recipe mode can estimate them.".format(len(unframed)), unframed))
    for wall in walls:
        if not library.section_for_wall(wall.get("type")):
            issues.append(Issue(
                "warning", "NO_WALL_SECTION",
                u"Wall type '{}' has no section in wall_recipes.json".format(wall.get("type")), [wall["id"]]))
    for code, item in library.items.items():
        if item.get("source") == "PROPOSED":
            issues.append(Issue(
                "info", "ITEM_TO_CONFIRM",
                u"Item {} uses a proposed value: {}".format(code, item.get("note") or item.get("name"))))

    order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: order.get(i.severity, 3))
    return issues, counted


# --- comparison --------------------------------------------------------------
def compare(counted, recipe, systems=None):
    """Recipe estimate vs counted framing, per LGS item, for the given systems."""
    def kg_by_item(takeoff):
        out = OrderedDict()
        for line in takeoff.lines.values():
            if line.group not in ("LGS profile",):
                continue
            if systems and line.system not in systems:
                continue
            out[line.item] = out.get(line.item, 0.0) + line.qty["kg"]
        return out

    a, b = kg_by_item(counted), kg_by_item(recipe)
    rows = []
    for item in sorted(set(a) | set(b)):
        c, r = a.get(item, 0.0), b.get(item, 0.0)
        rows.append((item, c, r, (r - c) / c if c else None))
    return rows
