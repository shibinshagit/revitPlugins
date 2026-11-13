# -*- coding: utf-8 -*-
"""UNIBOM reports: one dark / orange HTML design for the pyRevit output window
and for the saved report file, plus the CSV for Excel.

Layout: header, KPI strip, then tabs (Summary, BOQ, Systems, Members, Recipe
check, Issues). Tabs switch with a few lines of inline JavaScript, so the page
works in pyRevit's IE11 output window and in any browser as a saved file.

No Revit imports here: the caller passes a `link(ids, label)` function that
returns select-in-Revit links (unibom.ui.selection) or None for files.
"""
from __future__ import division

import datetime
import io
import os
import random
import re
from collections import OrderedDict

from unibom import engine

CSS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "report.css")

SYSTEM_ORDER = [
    "External wall panels", "Internal wall panels", "Floor panels", "WB panels",
    "Roof trusses", "Purlins", "Blocking", "Other panels",
    "Openings", "Fixings", "Roofing", "Not in a panel",
]
ROLE_ORDER = ["TBOT", "TTOP", "SV", "EV", "SD", "SW", "HDD", "HDW", "SBW", "CR",
              "HB", "BRR", "CB", "TopChord", "BottomChord", "Web", "Support", "Slider",
              "TRACK"]
# Columns shown for each BOQ group: (header, unit key, decimals)
GROUP_COLUMNS = OrderedDict([
    ("LGS profile", [("Pieces", "nr", 0), ("Length m", "m", 1), ("Weight kg", "kg", 1)]),
    ("LGS strap", [("Pieces", "nr", 0), ("Length m", "m", 1), ("Weight kg", "kg", 1)]),
    ("Board", [("Pieces", "nr", 0), ("Area m²", "m2", 2)]),
    ("Openings", [("Nr", "nr", 0), ("Area m²", "m2", 2)]),
    ("Fixings", [("Nr", "nr", 0)]),
    ("Roofing", [("Area m²", "m2", 2), ("Nr", "nr", 0)]),
])
DEFAULT_COLUMNS = [("Nr", "nr", 0), ("m", "m", 1), ("m²", "m2", 2), ("kg", "kg", 1)]
LGS_GROUPS = ("LGS profile", "LGS strap")


# --- formatting --------------------------------------------------------------
def esc(text):
    return (u"{}".format(text if text is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def fmt(value, digits=2):
    # IronPython 2.7 rejects "{:.2f}" on an int: always format a float.
    if value is None:
        return ""
    value = float(value)
    if abs(value - round(value)) < 1e-9:
        return u"{:,.0f}".format(value)
    return u"{:,.{}f}".format(value, digits)


def q(value, digits=2):
    return fmt(value, digits) if value else u""


def dash(value, digits=2):
    return fmt(value, digits) if value else u'<span class="ub-dim">—</span>'


def pct(value):
    return u"{:+.1%}".format(float(value)) if value is not None else u"n/a"


def stamp():
    return datetime.datetime.now()


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text or "model").strip("_") or "model"


def default_folder():
    base = os.path.join(os.path.expanduser("~"), "Documents", "UNIBOM")
    if not os.path.isdir(base):
        os.makedirs(base)
    return base


def _css():
    with io.open(CSS_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


def _version():
    try:
        from unibom import __version__
        return __version__
    except Exception:
        return ""


def _rank(order, value):
    return (order.index(value) if value in order else len(order), value or u"")


# --- page builder --------------------------------------------------------------
class Report(object):
    """Header + KPIs + tabbed panels. Blocks go to the current tab."""

    def __init__(self, title, model, chips, link=None):
        self.title = title
        self.model = model
        self.chips = chips
        self.link = link
        self.created = stamp()
        self.uid = "u{}".format(random.randint(100000, 999999))
        self.kpi_html = u""
        self.tabs = OrderedDict()
        self.current = None

    # structure -----------------------------------------------------------------
    def tab(self, key, title, badge=None, badge_cls=""):
        self.tabs[key] = {"title": title, "badge": badge, "badge_cls": badge_cls, "blocks": []}
        self.current = key

    def add(self, html):
        if self.current is None:
            self.tab("main", "Report")
        self.tabs[self.current]["blocks"].append(html)

    def select(self, ids):
        if not ids or self.link is None:
            return u""
        return self.link(ids, u"Select {:,}".format(len(ids)))

    # building blocks -------------------------------------------------------------
    def kpis(self, cards):
        html = [u'<div class="ub-kpis">']
        for card in cards:
            html.append(
                u'<div class="ub-kpi {cls}"><div class="ub-kpi-label">{label}</div>'
                u'<div class="ub-kpi-value">{value}<span class="ub-kpi-unit">{unit}</span></div>'
                u'<div class="ub-kpi-note">{note}</div></div>'.format(
                    cls=card.get("cls", ""), label=esc(card["label"]), value=esc(card["value"]),
                    unit=esc(card.get("unit", "")), note=esc(card.get("note", u" "))))
        html.append(u"</div>")
        self.kpi_html = u"".join(html)

    def card(self, title, body, aside=u"", flush=False):
        return (u'<div class="ub-card"><div class="ub-card-head"><span class="ub-card-title">{}</span>'
                u'<span class="ub-card-aside">{}</span></div><div class="ub-card-body{}">{}</div></div>').format(
                    esc(title), aside, u" flush" if flush else u"", body)

    def grid(self, left, right):
        return (u'<table class="ub-grid"><tr><td class="ub-grid-l">{}</td>'
                u'<td class="ub-grid-r">{}</td></tr></table>').format(left, right)

    def note(self, text):
        return u'<p class="ub-note">{}</p>'.format(text)

    def table(self, columns, rows, numeric=(), cls=u"", header=True):
        """columns: header labels; 'Select' columns vanish in file reports.
        rows: lists of html cells, ("group", text) or ("total", cells)."""
        keep = [i for i, c in enumerate(columns) if not (c == "Select" and self.link is None)]
        html = [u'<table class="ub-table {}">'.format(cls)]
        if header:
            html.append(u"<tr>")
            for i in keep:
                css = u' class="n"' if i in numeric else (u' class="s"' if columns[i] == "Select" else u"")
                html.append(u"<th{}>{}</th>".format(css, esc(columns[i])))
            html.append(u"</tr>")
        for row in rows:
            if isinstance(row, tuple) and row[0] == "group":
                html.append(u'<tr class="grp"><td colspan="{}">{}</td></tr>'.format(len(keep), row[1]))
                continue
            css = u""
            if isinstance(row, tuple) and row[0] == "total":
                css, row = u' class="tot"', row[1]
            html.append(u"<tr{}>".format(css))
            for i in keep:
                cell = row[i] if i < len(row) else u""
                td = u' class="n"' if i in numeric else (u' class="s"' if columns[i] == "Select" else u"")
                html.append(u"<td{}>{}</td>".format(td, cell))
            html.append(u"</tr>")
        html.append(u"</table>")
        return u"".join(html)

    # rendering -------------------------------------------------------------------
    def _tab_js(self, key):
        return (u"var r=document.getElementById('{u}');var d=r.getElementsByTagName('div');"
                u"for(var i=0;i<d.length;i++){{if(d[i].className=='ub-panel'){{d[i].style.display="
                u"(d[i].id=='{u}-{k}')?'block':'none';}}}}var s=this.parentNode.getElementsByTagName('span');"
                u"for(var j=0;j<s.length;j++){{if(s[j].className.indexOf('ub-tab')==0){{s[j].className='ub-tab';}}}}"
                u"this.className='ub-tab on';return false;").format(u=self.uid, k=key)

    def body_html(self):
        chips = u"".join(
            u'<span class="ub-chip">{} <b>{}</b></span>'.format(esc(k), esc(v)) for k, v in self.chips)
        head = (u'<div class="ub-head"><table class="ub-headrow"><tr>'
                u'<td class="ub-mark-cell"><span class="ub-mark">BOM</span></td>'
                u'<td><div class="ub-title">{}</div><div class="ub-sub">{} · {}</div></td>'
                u'<td class="ub-chips">{}</td></tr></table></div>').format(
                    esc(self.title), esc(self.model), self.created.strftime("%d %b %Y, %H:%M"), chips)
        tabs, panels = [], []
        for n, (key, tab) in enumerate(self.tabs.items()):
            badge = u""
            if tab["badge"] is not None:
                badge = u' <span class="ub-badge {}">{}</span>'.format(tab["badge_cls"], esc(tab["badge"]))
            tabs.append(u'<span class="ub-tab{}" onclick="{}">{}{}</span>'.format(
                u" on" if n == 0 else u"", self._tab_js(key), esc(tab["title"]), badge))
            panels.append(u'<div class="ub-panel" id="{}-{}" style="display:{}">{}</div>'.format(
                self.uid, key, u"block" if n == 0 else u"none", u"".join(tab["blocks"])))
        tabbar = u'<div class="ub-tabs">{}</div>'.format(u"".join(tabs)) if len(self.tabs) > 1 else u""
        foot = (u'<div class="ub-foot">UNIBOM {} · UNIQUBE · Quantities only; '
                u"pricing is applied in the estimate.</div>").format(_version())
        return (u'<div class="ub" id="{}" style="background:#16171b;color:#e8eaee;">{}{}{}{}{}</div>').format(
            self.uid, head, self.kpi_html, tabbar, u"".join(panels), foot)

    def show(self, output):
        """Render into the pyRevit output window."""
        try:
            output.set_width(1180)
        except Exception:
            pass
        css = _css()
        # pyRevit's output window is an IE engine: a <style> at the start of
        # inserted HTML is dropped. Put the CSS in <head>, and keep an inline
        # copy behind a leading element as a fallback.
        try:
            output.add_style(css)
        except Exception:
            pass
        output.print_html(u'<span style="display:none">&nbsp;</span><style>{}</style>{}'.format(
            css, self.body_html()))

    def save(self, path):
        doc = (u'<!DOCTYPE html><html><head><meta charset="utf-8"><title>{}</title>'
               u'<meta http-equiv="X-UA-Compatible" content="IE=edge">'
               u"<style>{}</style></head><body>{}</body></html>").format(
                   esc(self.title + " - " + self.model), _css(), self.body_html())
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(doc)
        return path


# --- content: BOQ report ---------------------------------------------------------
def _ids(rows):
    out = []
    for r in rows:
        out.extend(r.ids)
    return out


def _bar(value, top):
    if not value:
        return u""
    width = 0 if not top else max(1.5, 100.0 * float(value) / float(top))
    return (u'<div class="ub-bar"><div class="ub-bar-fill" style="width:{:.1f}%"></div></div>').format(
        width if value else 0)


def _kpis(rep, takeoff, issues):
    lgs = takeoff.lgs_summary()
    items = takeoff.by_item()
    boards = takeoff.board_summary()
    errors = len([i for i in issues if i.severity == "error"])
    warnings = len([i for i in issues if i.severity == "warning"])
    cards = [
        {"label": "Net LGS", "value": fmt(lgs["net_kg"], 0), "unit": "kg", "cls": "accent",
         "note": u"{} m of profile".format(fmt(sum(r.qty["m"] for r in items if r.group == "LGS profile"), 0))},
        {"label": "Gross LGS", "value": fmt(lgs["gross_kg"], 0), "unit": "kg",
         "note": u"incl. {:.0%} wastage".format(float(lgs["wastage"]))},
    ]
    if takeoff.mode == "counted":
        cards += [
            {"label": "Boards", "value": fmt(sum(b[1] for b in boards), 0), "unit": u"m²",
             "note": u"{:,} sheets to order".format(sum(b[3] for b in boards))},
            {"label": "Openings", "value": fmt(sum(r.qty["nr"] for r in items if r.group == "Openings")),
             "unit": "nr", "note": "windows and doors"},
            {"label": "Fixings", "value": fmt(sum(r.qty["nr"] for r in items if r.group == "Fixings")),
             "unit": "nr", "note": "anchors and connectors"},
        ]
    cards.append({"label": "To check", "value": u"{}".format(errors + warnings),
                  "cls": "bad" if errors else ("warn" if warnings else "good"),
                  "note": u"{} error(s), {} warning(s)".format(errors, warnings)})
    rep.kpis(cards)


def _summary_tab(rep, takeoff, issues, files):
    lib = takeoff.library
    lgs = takeoff.lgs_summary()
    lgs_rows = [
        [u"Net LGS", fmt(lgs["net_kg"]) + u" kg"],
        [u"Wastage", u"{:.0%}".format(float(lgs["wastage"]))],
        ("total", [u"Gross LGS to supply", fmt(lgs["gross_kg"]) + u" kg"]),
        [u"Screws <span class=\"ub-dim\">({} per kg net)</span>".format(fmt(lib.param("screws_per_kg"))),
         fmt(lgs["screws_nr"], 0) + u" nr"],
    ]
    left = rep.card("LGS", rep.table(["", "Value"], lgs_rows, numeric=(1,), cls="kv", header=False), flush=True)
    boards = takeoff.board_summary()
    if boards:
        board_rows = [[esc(b[0]), fmt(b[1]), fmt(b[2]), u"{:,}".format(b[3])] for b in boards]
        board_rows.append(("total", [u"Total", fmt(sum(b[1] for b in boards)), fmt(sum(b[2] for b in boards)),
                                     u"{:,}".format(sum(b[3] for b in boards))]))
        left += rep.card("Boards", rep.table(["Board", u"Net m²", "+ wastage", "Sheets"], board_rows,
                                             numeric=(1, 2, 3)), flush=True,
                         aside=u"sheets 1200 × 2400, {:.0%} wastage".format(float(lib.param("board_wastage"))))

    systems = _systems(takeoff)
    top = max([s["kg"] for s in systems] or [0])
    sys_rows = [[esc(s["name"]), _bar(s["kg"], top), dash(s["kg"], 0)] for s in systems if s["kg"]]
    right = rep.card("LGS by system", rep.table(["System", "", "kg"], sys_rows, numeric=(2,), cls="bars"),
                     flush=True, aside=u"{} kg net".format(fmt(lgs["net_kg"], 0)))
    if issues:
        top_issues = u"".join(
            u'<div class="ub-issue"><span class="ub-pill {0}">{1}</span> {2}</div>'.format(
                i.severity, i.severity.upper(), esc(i.message)) for i in issues[:4])
        more = u'<div class="ub-dim ub-more">+ {} more in Issues</div>'.format(len(issues) - 4) if len(issues) > 4 else u""
        right += rep.card("To check", top_issues + more)
    rep.add(rep.grid(left, right))
    if files:
        rep.add(rep.card("Saved files", rep.table(["File", ""], [
            [esc(os.path.basename(p)), u'<a href="file:///{}">Open</a>'.format(esc(p.replace("\\", "/")))]
            for p in files]), flush=True))


UNIT_LABEL = {"kg": u"kg", "m": u"m", "m2": u"m²", "nr": u"nr"}


def _boq_cards(rep, takeoff, prefix=u""):
    by_group = OrderedDict()
    for r in takeoff.by_item():
        by_group.setdefault(r.group, []).append(r)
    sheets = dict((b[0], b[3]) for b in takeoff.board_summary())
    out = []
    for group, rows in by_group.items():
        cols = GROUP_COLUMNS.get(group, DEFAULT_COLUMNS)
        headers = ["Item", "Description"] + [c[0] for c in cols]
        if group == "Board":
            headers.append("Sheets")
        headers.append("Select")
        numeric = tuple(range(2, 2 + len(cols) + (1 if group == "Board" else 0)))
        body = []
        for r in rows:
            cells = [u'<span class="ub-code">{}</span>'.format(esc(r.item)), esc(r.name)]
            cells += [dash(r.qty[c[1]], c[2]) for c in cols]
            if group == "Board":
                cells.append(u"{:,}".format(sheets.get(r.item, 0)))
            cells.append(rep.select(r.ids))
            body.append(cells)
        if len(rows) > 1:
            total = [u"Total", u""] + [dash(sum(r.qty[c[1]] for r in rows), c[2]) for c in cols]
            if group == "Board":
                total.append(u"{:,}".format(sum(sheets.get(r.item, 0) for r in rows)))
            total.append(rep.select(_ids(rows)))
            body.append(("total", total))
        main = [c for c in cols if c[1] == "kg"] or [c for c in cols if c[1] == "m2"] or cols[:1]
        aside = u"{} {}".format(fmt(sum(r.qty[main[0][1]] for r in rows), main[0][2]), UNIT_LABEL[main[0][1]]) \
            if main else u""
        out.append(rep.card(prefix + group, rep.table(headers, body, numeric=numeric), aside=esc(aside), flush=True))
    return u"".join(out)


def _systems(takeoff):
    out = []
    for name, rows in takeoff.by_system().items():
        out.append({
            "name": name, "rows": rows, "ids": _ids(rows),
            "kg": sum(r.qty["kg"] for r in rows if r.group in LGS_GROUPS),
            "m2": sum(r.qty["m2"] for r in rows if r.group in ("Board", "Roofing")),
            "nr": sum(r.qty["nr"] for r in rows if r.group in ("Openings", "Fixings")),
            "profiles": u", ".join(sorted(set(r.item for r in rows if r.group == "LGS profile"))),
        })
    out.sort(key=lambda s: _rank(SYSTEM_ORDER, s["name"]))
    return out


def _systems_tab(rep, takeoff):
    systems = _systems(takeoff)
    top = max([s["kg"] for s in systems] or [0])
    rows = [[u"<b>{}</b>".format(esc(s["name"])), _bar(s["kg"], top), dash(s["kg"], 1), dash(s["m2"], 2),
             dash(s["nr"], 0), u'<span class="ub-dim">{}</span>'.format(esc(s["profiles"])) if s["profiles"] else u"",
             rep.select(s["ids"])] for s in systems]
    rep.add(rep.card("Systems", rep.table(
        ["System", "LGS share", "LGS kg", u"Area m²", "Openings + fixings", "Profiles", "Select"],
        rows, numeric=(2, 3, 4), cls="bars"), flush=True,
        aside=u"panel systems first, then items outside panels"))


def _members_tab(rep, takeoff):
    lib = takeoff.library
    by_system = OrderedDict()
    for line in takeoff.lines.values():
        if line.group == "LGS profile":
            by_system.setdefault(line.system, []).append(line)
    for system in sorted(by_system, key=lambda s: _rank(SYSTEM_ORDER, s)):
        lines = sorted(by_system[system], key=lambda l: (_rank(ROLE_ORDER, l.role), l.item))
        body = [[esc(lib.role_name(l.role) or u"(no role)"), u'<span class="ub-code">{}</span>'.format(esc(l.role or u"")),
                 u'<span class="ub-dim">{}</span>'.format(esc(l.item)), dash(l.qty["nr"], 0), dash(l.qty["m"], 1),
                 dash(l.qty["kg"], 1), rep.select(l.ids)] for l in lines]
        kg = sum(l.qty["kg"] for l in lines)
        body.append(("total", [u"Total", u"", u"", dash(sum(l.qty["nr"] for l in lines), 0),
                               dash(sum(l.qty["m"] for l in lines), 1), dash(kg, 1), rep.select(_ids(lines))]))
        rep.add(rep.card(system, rep.table(["Member", "Code", "Profile", "Pieces", "Length m", "Weight kg", "Select"],
                                           body, numeric=(3, 4, 5)), aside=u"{} kg".format(fmt(kg, 0)), flush=True))


def _compare_html(rep, rows, systems):
    body = []
    for item, counted, recipe, delta in rows:
        cls = "ub-neg" if delta is not None and delta < 0 else "ub-pos"
        body.append([u'<span class="ub-code">{}</span>'.format(esc(item)), fmt(counted, 1), fmt(recipe, 1),
                     u'<span class="{}">{}</span>'.format(cls, pct(delta))])
    return rep.card("Recipe vs counted", rep.table(["Profile", "Counted kg", "Recipe kg", "Difference"],
                                                   body, numeric=(1, 2, 3)), flush=True,
                    aside=esc(u", ".join(systems)))


def _issues_tab(rep, issues):
    if not issues:
        rep.add(rep.card("Issues", u'<div class="ub-empty">No issues found. The BOQ can be used.</div>'))
        return
    rows = [[u'<span class="ub-pill {0}">{1}</span>'.format(i.severity, i.severity.upper()),
             u'<span class="ub-code">{}</span>'.format(esc(i.code)), esc(i.message), rep.select(i.ids)]
            for i in issues]
    errors = len([i for i in issues if i.severity == "error"])
    warnings = len([i for i in issues if i.severity == "warning"])
    rep.add(rep.card("Issues", rep.table(["Severity", "Code", "What to check", "Select"], rows, cls="issues"),
                     aside=u"{} error(s) make the BOQ wrong · {} warning(s) may".format(errors, warnings),
                     flush=True))


def _issue_badge(issues):
    errors = len([i for i in issues if i.severity == "error"])
    warnings = len([i for i in issues if i.severity == "warning"])
    if errors:
        return str(errors + warnings), "bad"
    if warnings:
        return str(warnings), "warn"
    return "0", "good"


def boq_report(model, chips, counted, recipe, issues, link=None, files=None, wall_systems=None):
    """The Run BOQ report: tabs for summary, BOQ, systems, members, recipe, issues."""
    rep = Report("BOQ takeoff", model, chips, link)
    main = counted or recipe
    _kpis(rep, main, issues)

    rep.tab("summary", "Summary")
    _summary_tab(rep, main, issues, files)

    rep.tab("boq", "BOQ", u"{}".format(len(main.by_item())))
    rep.add(_boq_cards(rep, main))

    rep.tab("systems", "Systems")
    _systems_tab(rep, main)

    if counted:
        rep.tab("members", "Members")
        _members_tab(rep, counted)

    if recipe:
        rep.tab("recipe", "Recipe check" if counted else "Recipe estimate")
        if counted:
            rep.add(_compare_html(rep, engine.compare(counted, recipe, wall_systems), wall_systems or []))
            rep.add(rep.note(u"Recipe = spec formulas from wall length, height and openings. Counted = the "
                             u"framing BIMSF modelled. The recipe does not yet count junction studs, headers, "
                             u"cripples or bracing."))
        rep.add(_boq_cards(rep, recipe, u"Recipe · "))

    badge, cls = _issue_badge(issues)
    rep.tab("issues", "Issues", badge, cls)
    _issues_tab(rep, issues)
    return rep


def check_report(model, chips, counted, walls, issues, link=None):
    """The Check Model report: KPIs and the issue list."""
    rep = Report("Model check", model, chips, link)
    errors = len([i for i in issues if i.severity == "error"])
    warnings = len([i for i in issues if i.severity == "warning"])
    framed = sum(1 for w in walls if w.get("container"))
    rep.kpis([
        {"label": "Errors", "value": str(errors), "cls": "bad" if errors else "good", "note": "make the BOQ wrong"},
        {"label": "Warnings", "value": str(warnings), "cls": "warn" if warnings else "good",
         "note": "may make it wrong"},
        {"label": "Elements read", "value": u"{:,}".format(counted.source_count),
         "note": u"{:,} BIMSF helpers ignored".format(counted.ignored)},
        {"label": "Walls framed", "value": u"{} / {}".format(framed, len(walls)), "note": "by BIMSF"},
        {"label": "Net LGS", "value": fmt(counted.lgs_summary()["net_kg"], 0), "unit": "kg",
         "note": u"{} BOQ items".format(len(counted.by_item()))},
    ])
    rep.tab("issues", "Issues")
    _issues_tab(rep, issues)
    rep.add(rep.note(u"Fix the model, or the rules in Setup &gt; Rules, then run Check Model again. "
                     u"Run BOQ when there are no errors."))
    return rep


# --- CSV ---------------------------------------------------------------------
def _csv_cell(value):
    text = u"{}".format(value if value is not None else "")
    if any(c in text for c in (",", '"', "\n")):
        text = u'"{}"'.format(text.replace('"', '""'))
    return text


def _num(value):
    return u"{:.3f}".format(float(value)) if value else u""


def output_path(folder, project, mode, ext, when=None):
    when = when or stamp()
    return os.path.join(folder or default_folder(), u"UNIBOM_{}_{}_{}.{}".format(
        safe_name(project), mode, when.strftime("%Y%m%d-%H%M"), ext))


def write_csv(takeoff, project, folder=None, when=None):
    """One row per system x item x role. Opens straight in Excel (UTF-8 BOM)."""
    path = output_path(folder, project, takeoff.mode, "csv", when)
    lib = takeoff.library
    header = [u"Mode", u"System", u"Group", u"Item", u"Description", u"Role",
              u"Nr", u"m", u"m2", u"kg", u"Element count", u"Element ids"]
    lines = [u",".join(header)]
    ordered = sorted(takeoff.lines.values(),
                     key=lambda l: (l.system,) + engine.group_key(l.group, l.item) + (l.role,))
    for line in ordered:
        lines.append(u",".join(_csv_cell(v) for v in [
            takeoff.mode, line.system, line.group, line.item, line.name,
            lib.role_name(line.role), _num(line.qty["nr"]), _num(line.qty["m"]),
            _num(line.qty["m2"]), _num(line.qty["kg"]), len(line.ids),
            u" ".join(str(i) for i in line.ids[:2000]),
        ]))
    lgs = takeoff.lgs_summary()
    if lgs["net_kg"]:
        lines.append(u"")
        lines.append(u"Summary,,,LGS net kg,,,,,,{}".format(_num(lgs["net_kg"])))
        lines.append(u"Summary,,,LGS gross kg (wastage {:.0%}),,,,,,{}".format(float(lgs["wastage"]), _num(lgs["gross_kg"])))
        lines.append(u"Summary,,,SCREW-LGS,,,{},,,".format(_num(lgs["screws_nr"])))
    for item, net, gross, sheets in takeoff.board_summary():
        lines.append(u"Summary,,,{} sheets 1200x2400 (with wastage),,,{},,{},".format(item, sheets, _num(gross)))
    with io.open(path, "w", encoding="utf-8-sig") as fh:
        fh.write(u"\r\n".join(lines) + u"\r\n")
    return path
