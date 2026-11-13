# -*- coding: utf-8 -*-
"""UNIBOM regression tests. Run outside Revit with CPython 3:

    python3 tools/unibom_tests/test_unibom.py

The fixture is a record dump of UAE_26-005 ZUHA Admin Building (11 Nov 2026),
in the exact shape unibom.collect produces. If a rule change moves a total,
these tests say by how much, so the change is a decision and not an accident.
"""
from __future__ import division, print_function

import io
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "Uniqube.extension", "lib"))

from unibom import engine, formula  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "zuha_26-005_records.json")


def load_fixture():
    with io.open(FIXTURE, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    return data["members"], data["walls"]


class FormulaTests(unittest.TestCase):
    def test_arithmetic(self):
        self.assertEqual(formula.evaluate("2 * -3 + 4^2", {}), 10)
        self.assertEqual(formula.evaluate("ceil(1061 / P.x)", {"P": {"x": 2400}}), 1)

    def test_vars_chain(self):
        scope = formula.evaluate_vars(
            [["n", "ceil(L / 2400)"], ["Lp", "L / n"], ["studs", "n * (ceil(Lp / 600) + 1)"]],
            {"L": 5000})
        self.assertEqual(scope["studs"], 12)

    def test_rejects_code(self):
        for bad in ("__import__(1)", "L.__class__", "open('x')", "1 +"):
            with self.assertRaises(formula.FormulaError):
                formula.evaluate(bad, {"L": 1})


class ZuhaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.members, cls.walls = load_fixture()
        cls.lib = engine.Library()
        cls.issues, cls.counted = engine.check_model(cls.members, cls.walls, cls.lib)
        cls.items = dict((r.item, r) for r in cls.counted.by_item())

    def test_no_errors(self):
        errors = [i.message for i in self.issues if i.severity == "error"]
        self.assertEqual(errors, [])

    def test_every_member_has_a_rule(self):
        self.assertFalse([i for i in self.issues if i.code == "NO_RULE"])

    def test_lgs_totals(self):
        lgs = self.counted.lgs_summary()
        self.assertAlmostEqual(lgs["net_kg"], 29195.0, delta=1.0)
        self.assertAlmostEqual(self.items["LGS-362S162-54"].qty["m"], 8242.53, delta=0.1)
        self.assertAlmostEqual(self.items["LGS-600S162-54"].qty["m"], 3896.43, delta=0.1)
        self.assertEqual(self.items["LGS-362S162-54"].qty["nr"], 4595)

    def test_boards(self):
        self.assertAlmostEqual(self.items["BOARD-12"].qty["m2"], 439.19, delta=0.1)
        self.assertAlmostEqual(self.items["BOARD-12.5"].qty["m2"], 971.11, delta=0.1)
        self.assertAlmostEqual(self.items["BOARD-16"].qty["m2"], 432.65, delta=0.1)

    def test_counts(self):
        self.assertEqual(self.items["ANCHOR-M6 - 100mm EMBEDMENT"].qty["nr"], 626)
        self.assertEqual(self.items["WIN-Aluminium Window-1500 x 1400"].qty["nr"], 13)

    def test_recipe_runs_on_all_walls(self):
        recipe = engine.run_recipes(self.walls, self.lib)
        self.assertEqual(recipe.source_count, 177)
        rows = engine.compare(self.counted, recipe, ["External wall panels", "Internal wall panels"])
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
