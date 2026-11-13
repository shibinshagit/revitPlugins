# -*- coding: utf-8 -*-
"""Safe arithmetic for UNIBOM rule formulas. Never uses eval().

Grammar (same as superstructureRules.json):
    expr    := term (('+' | '-') term)*
    term    := factor (('*' | '/') factor)*
    factor  := ('-' | '+') factor | power
    power   := atom ('^' factor)?
    atom    := number | name | name '(' args ')' | '(' expr ')'
    name    := identifier ('.' identifier)*      e.g. L, P.stud_spacing_mm

Functions: ceil, floor, round, min, max, sqrt, abs, cos, tan, sin (degrees).
Works in IronPython 2.7 (pyRevit) and CPython 3 (tests, server ports).
"""
from __future__ import division

import math
import re

_TOKEN = re.compile(
    r"\s*(?:(\d+\.\d*|\.\d+|\d+)|([A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*)|(.))"
)

FUNCTIONS = {
    "ceil": lambda x: float(math.ceil(x)),
    "floor": lambda x: float(math.floor(x)),
    "round": lambda x, n=0: round(x, int(n)),
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "abs": abs,
    "cos": lambda d: math.cos(math.radians(d)),
    "sin": lambda d: math.sin(math.radians(d)),
    "tan": lambda d: math.tan(math.radians(d)),
}


class FormulaError(Exception):
    pass


def tokenize(text):
    tokens = []
    pos = 0
    text = text or ""
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            break
        num, name, op = m.groups()
        if num is not None:
            tokens.append(("num", float(num)))
        elif name is not None:
            tokens.append(("name", name))
        elif op is not None and op.strip():
            tokens.append(("op", op))
        pos = m.end()
    return tokens


class _Parser(object):
    def __init__(self, text, names):
        self.text = text
        self.tokens = tokenize(text)
        self.i = 0
        self.names = names

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else (None, None)

    def take(self, kind=None, value=None):
        tok = self.peek()
        if tok[0] is None:
            raise FormulaError("Unexpected end of formula: {}".format(self.text))
        if kind and tok[0] != kind or value and tok[1] != value:
            raise FormulaError(
                "Expected {} in formula: {}".format(value or kind, self.text)
            )
        self.i += 1
        return tok

    def parse(self):
        value = self.expr()
        if self.i != len(self.tokens):
            raise FormulaError("Unexpected '{}' in formula: {}".format(
                self.peek()[1], self.text))
        return value

    def expr(self):
        value = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            rhs = self.term()
            value = value + rhs if op == "+" else value - rhs
        return value

    def term(self):
        value = self.factor()
        while self.peek() in (("op", "*"), ("op", "/")):
            op = self.take()[1]
            rhs = self.factor()
            if op == "*":
                value = value * rhs
            else:
                if rhs == 0:
                    raise FormulaError("Division by zero in formula: {}".format(self.text))
                value = value / rhs
        return value

    def factor(self):
        if self.peek() == ("op", "-"):
            self.take()
            return -self.factor()
        if self.peek() == ("op", "+"):
            self.take()
            return self.factor()
        return self.power()

    def power(self):
        base = self.atom()
        if self.peek() == ("op", "^"):
            self.take()
            return base ** self.factor()
        return base

    def atom(self):
        kind, value = self.peek()
        if kind == "num":
            self.take()
            return value
        if kind == "op" and value == "(":
            self.take()
            inner = self.expr()
            self.take("op", ")")
            return inner
        if kind == "name":
            self.take()
            if self.peek() == ("op", "("):
                fn = FUNCTIONS.get(value)
                if fn is None:
                    raise FormulaError("Unknown function '{}' in: {}".format(value, self.text))
                self.take()
                args = []
                if self.peek() != ("op", ")"):
                    args.append(self.expr())
                    while self.peek() == ("op", ","):
                        self.take()
                        args.append(self.expr())
                self.take("op", ")")
                return float(fn(*args))
            return self.lookup(value)
        raise FormulaError("Unexpected '{}' in formula: {}".format(value, self.text))

    def lookup(self, dotted):
        parts = dotted.split(".")
        node = self.names
        for part in parts:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                raise FormulaError("Unknown name '{}' in: {}".format(dotted, self.text))
        if isinstance(node, bool):
            return 1.0 if node else 0.0
        if isinstance(node, (int, float)):
            return float(node)
        raise FormulaError("'{}' is not a number in: {}".format(dotted, self.text))


def evaluate(text, names):
    """Evaluate one formula against a dict of names (nested dicts allowed)."""
    if isinstance(text, (int, float)):
        return float(text)
    return _Parser(str(text), names).parse()


def evaluate_vars(var_list, names):
    """Evaluate ordered [name, formula] pairs; each result is visible to the next."""
    scope = dict(names)
    for pair in var_list or []:
        name, formula = pair[0], pair[1]
        scope[name] = evaluate(formula, scope)
    return scope
