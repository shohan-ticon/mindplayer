"""
sample_backtrace.py - exercise deep call stacks via a recursive-descent
expression evaluator, so the recorded trace has interesting backtraces
to scrub through.

Run:   tracesnap record examples/sample_backtrace.py --out trace.json
Open:  tracesnap view trace.json

What this exercises that the simpler samples don't:
  * Mutual recursion across parse_expr / parse_term / parse_factor -- a
    single token like "2" can sit 6+ frames deep on the stack.
  * A second recursion (eval_node) that walks the AST the parser built,
    so depth increases again on a separate code path.
  * An unwind path: divide-by-zero is detected inside eval_node and
    raised; it bubbles up through evaluate() -> run_one() and is caught
    in main(). The recorder marks each of those `return` events as an
    unwind so the player shows the stack peeling back.
  * A "secret"-named local in the audit step so the redaction layer
    fires on something that isn't a function argument.

Offline-friendly: no network calls. Pure stdlib.
"""
from __future__ import annotations


# ---------- tokenizer ------------------------------------------------------

def tokenize(src):
    tokens = []
    i = 0
    while i < len(src):
        ch = src[i]
        if ch == " ":
            i = i + 1
        elif ch in "+-*/()":
            tokens = tokens + [(ch, ch)]
            i = i + 1
        elif ch.isdigit():
            j = i
            while j < len(src) and src[j].isdigit():
                j = j + 1
            tokens = tokens + [("num", int(src[i:j]))]
            i = j
        else:
            raise ValueError(f"unexpected char {ch!r} at {i}")
    tokens = tokens + [("end", None)]
    return tokens


# ---------- parser (mutual recursion) -------------------------------------
#
# Grammar:
#   expr   := term   (("+" | "-") term)*
#   term   := factor (("*" | "/") factor)*
#   factor := NUMBER | "(" expr ")"
#
# Each parse_* returns (node, next_index). Nodes are tuples:
#   ("num", n) | ("bin", op, left, right)


def parse_expr(tokens, pos):
    node, pos = parse_term(tokens, pos)
    while tokens[pos][0] in ("+", "-"):
        op = tokens[pos][0]
        right, pos = parse_term(tokens, pos + 1)
        node = ("bin", op, node, right)
    return node, pos


def parse_term(tokens, pos):
    node, pos = parse_factor(tokens, pos)
    while tokens[pos][0] in ("*", "/"):
        op = tokens[pos][0]
        right, pos = parse_factor(tokens, pos + 1)
        node = ("bin", op, node, right)
    return node, pos


def parse_factor(tokens, pos):
    kind, val = tokens[pos]
    if kind == "num":
        return ("num", val), pos + 1
    if kind == "(":
        node, pos = parse_expr(tokens, pos + 1)
        if tokens[pos][0] != ")":
            raise ValueError(f"expected ')' at token {pos}")
        return node, pos + 1
    raise ValueError(f"unexpected token {tokens[pos]!r} at {pos}")


def parse(src):
    tokens = tokenize(src)
    node, pos = parse_expr(tokens, 0)
    if tokens[pos][0] != "end":
        raise ValueError(f"trailing tokens at {pos}: {tokens[pos:]!r}")
    return node


# ---------- evaluator (second recursion over the AST) ---------------------


def eval_node(node, depth):
    kind = node[0]
    if kind == "num":
        return node[1]
    # ("bin", op, left, right) -- recurse, then combine.
    _, op, left, right = node
    lv = eval_node(left, depth + 1)
    rv = eval_node(right, depth + 1)
    if op == "+":
        return lv + rv
    if op == "-":
        return lv - rv
    if op == "*":
        return lv * rv
    if op == "/":
        if rv == 0:
            # Raised from arbitrary depth -- bubbles back through
            # eval_node -> evaluate -> run_one and is caught in main().
            raise ZeroDivisionError(f"divide by zero evaluating {left!r} / {right!r}")
        return lv // rv
    raise ValueError(f"unknown op {op!r}")


def evaluate(tree):
    return eval_node(tree, depth=0)


# ---------- audit step (redaction demo) -----------------------------------


def audit(label, value):
    # `secret` is a redacted name, so its assign event shows <redacted>
    # in the player even though it is a plain local, not a call arg.
    secret = "sig-" + str(abs(hash(label)) % 100000)
    record = {"label": label, "value": value, "sig": secret[:6]}
    return record


# ---------- driver --------------------------------------------------------


def run_one(label, src):
    tree = parse(src)
    value = evaluate(tree)
    return audit(label, value)


def main():
    cases = [
        ("simple",      "1 + 2 * 3"),               # exercises +, *
        ("parens",      "(4 + 5) * (6 - 2)"),       # nested parens, both arms
        ("deep",        "((1+2)+(3+4))*((5-1)/(2))"),
        ("blows_up",    "10 / (3 - 3)"),            # raises ZeroDivisionError
        ("after_error", "100 - 50"),                # proves recording continues
    ]
    results = []
    errors = []
    for label, src in cases:
        try:
            results = results + [run_one(label, src)]
        except (ZeroDivisionError, ValueError) as exc:
            # Each frame on the way up emits an unwind `return` event;
            # this `except` is where the unwind chain terminates.
            errors = errors + [{"label": label, "src": src, "error": str(exc)}]
    summary = {
        "ok_count":    len(results),
        "error_count": len(errors),
        "last_value":  results[-1]["value"] if results else None,
        "first_error": errors[0]["error"] if errors else None,
    }
    return summary, results, errors


result = main()
