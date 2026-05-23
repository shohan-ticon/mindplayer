"""
main_calc.py - entrypoint for the calculator demo.

Each operation lives in its own file in this directory:
  add_op.py       -> Add
  subtract_op.py  -> Subtract
  multiply_op.py  -> Multiply
  divide_op.py    -> Divide

Run:   tracesnap record examples/calculator/main_calc.py --out trace.json
Open:  tracesnap view trace.json

Note: tracesnap traces only the entrypoint file, so the Add/Subtract/
Multiply/Divide methods will NOT appear in the trace -- you'll only see
Main.run, Main.take_input, and the calls into the imported classes
(as call sites, not as traced frames).
"""
import os
import sys

# Make the sibling modules importable when this file is run via
# `tracesnap record examples/calculator/main_calc.py` from the repo root.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from add_op import Add
from subtract_op import Subtract
from multiply_op import Multiply
from divide_op import Divide


class Main:
    def __init__(self):
        self.a = None
        self.b = None

    def take_input(self):
        self.a = int(input("Enter first number: "))
        self.b = int(input("Enter second number: "))

    def do_add(self):
        op = Add(self.a, self.b)
        return op.compute()

    def do_subtract(self):
        op = Subtract(self.a, self.b)
        return op.compute()

    def do_multiply(self):
        op = Multiply(self.a, self.b)
        return op.compute()

    def do_divide(self):
        op = Divide(self.a, self.b)
        try:
            return op.compute()
        except ZeroDivisionError as exc:
            return f"ERROR: {exc}"

    def run(self):
        self.take_input()
        results = {
            "add":      self.do_add(),
            "subtract": self.do_subtract(),
            "multiply": self.do_multiply(),
            "divide":   self.do_divide(),
        }
        for name, value in results.items():
            print(f"{name:<9}= {value}")
        return results


result = Main().run()
