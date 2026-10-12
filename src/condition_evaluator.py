"""Safe expression / condition evaluator.

Workflow conditions, row rules and value mappings are written as small
expressions (e.g. ``row.current_stock < row.minimum_stock``). They are parsed
by the hand-written tokenizer + recursive-descent parser below into an AST and
evaluated against an explicit *scope* dictionary.

Nothing here uses ``eval``/``exec``/``compile`` or attribute access on Python
objects: names resolve only through the scope dict, paths traverse only
``dict`` keys and ``list`` indexes, and only whitelisted functions can be called.

Expression schema (documented in README -> "How conditions and branching work")
------------------------------------------------------------------------------
Literals     numbers ``12``, ``0.5``; strings ``'x'`` / ``"x"``; ``true`` ``false``
             ``null``; lists ``[1, 2, 'a']``
Paths        ``inputs.threshold``  ``steps.load.rows[0].sku``  ``row["Unit Price"]``
Arithmetic   ``+ - * / %`` (``+`` also concatenates two strings), unary ``-``
Comparison   ``< <= > >= == != in not in`` (ordering needs two numbers or two
             strings; ordering with ``null`` is an error, never silently false)
Logic        ``and or not`` (short-circuit)
Functions    see :data:`FUNCTION_DOCS`
Templates    ``"{{ expr }}"`` inside strings, rendered by :func:`render_template`

Parameters in a workflow spec that start with ``=`` are expressions evaluated
by the engine (typed result); strings containing ``{{ }}`` are templates.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from typing import Any, Callable, Iterable

from .errors import ExpressionError, ExpressionSyntaxError, MissingValueError

MAX_EXPRESSION_LENGTH = 800
MAX_DEPTH = 40
MAX_LIST_SIZE = 100_000

# ----------------------------------------------------------------------------
# Tokenizer
# ----------------------------------------------------------------------------
_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<number>\d+(?:\.\d+)?)
  | (?P<string>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op><=|>=|==|!=|<|>|\+|-|\*|/|%|\(|\)|\[|\]|\.|,)
    """,
    re.VERBOSE,
)
_KEYWORDS = {"and", "or", "not", "in", "true", "false", "null", "True", "False", "None"}
_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", "'": "'", '"': '"'}


@dataclass(frozen=True)
class Token:
    kind: str  # number | string | name | op | kw | end
    value: Any
    pos: int


def _tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            hint = " (use '==' to compare, there is no assignment)" if text[pos] == "=" else ""
            raise ExpressionSyntaxError(
                f"Unexpected character {text[pos]!r} at position {pos}{hint}",
                detail=text,
            )
        kind = m.lastgroup
        raw = m.group()
        if kind == "ws":
            pass
        elif kind == "number":
            tokens.append(Token("number", float(raw) if "." in raw else int(raw), pos))
        elif kind == "string":
            body = raw[1:-1]
            tokens.append(Token("string", re.sub(r"\\(.)", lambda mm: _ESCAPES.get(mm.group(1), mm.group(1)), body), pos))
        elif kind == "name":
            tokens.append(Token("kw" if raw in _KEYWORDS else "name", raw, pos))
        else:
            tokens.append(Token("op", raw, pos))
        pos = m.end()
    tokens.append(Token("end", None, len(text)))
    return tokens


# ----------------------------------------------------------------------------
# AST
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Const:
    value: Any


@dataclass(frozen=True)
class Name:
    name: str


@dataclass(frozen=True)
class Attr:
    obj: Any
    key: str


@dataclass(frozen=True)
class Index:
    obj: Any
    index: Any


@dataclass(frozen=True)
class Call:
    func: str
    args: tuple


@dataclass(frozen=True)
class Unary:
    op: str
    operand: Any


@dataclass(frozen=True)
class Binary:
    op: str
    left: Any
    right: Any


@dataclass(frozen=True)
class Compare:
    op: str
    left: Any
    right: Any


@dataclass(frozen=True)
class BoolOp:
    op: str  # and | or
    values: tuple


@dataclass(frozen=True)
class ListLit:
    items: tuple


# ----------------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------------
class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.tokens = _tokenize(text)
        self.i = 0
        self.depth = 0

    # helpers
    @property
    def cur(self) -> Token:
        return self.tokens[self.i]

    def _next(self) -> Token:
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def _is_op(self, *ops: str) -> bool:
        return self.cur.kind == "op" and self.cur.value in ops

    def _is_kw(self, *kws: str) -> bool:
        return self.cur.kind == "kw" and self.cur.value in kws

    def _expect_op(self, op: str) -> None:
        if not self._is_op(op):
            self._fail(f"Expected {op!r}")
        self._next()

    def _fail(self, msg: str):
        tok = self.cur
        found = "end of expression" if tok.kind == "end" else repr(tok.value)
        raise ExpressionSyntaxError(f"{msg} but found {found} at position {tok.pos}", detail=self.text)

    def _enter(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ExpressionSyntaxError("Expression is nested too deeply", detail=self.text)

    # grammar
    def parse(self):
        node = self.or_expr()
        if self.cur.kind != "end":
            self._fail("Unexpected trailing input")
        return node

    def or_expr(self):
        self._enter()
        parts = [self.and_expr()]
        while self._is_kw("or"):
            self._next()
            parts.append(self.and_expr())
        self.depth -= 1
        return parts[0] if len(parts) == 1 else BoolOp("or", tuple(parts))

    def and_expr(self):
        parts = [self.not_expr()]
        while self._is_kw("and"):
            self._next()
            parts.append(self.not_expr())
        return parts[0] if len(parts) == 1 else BoolOp("and", tuple(parts))

    def not_expr(self):
        if self._is_kw("not"):
            self._next()
            self._enter()
            node = Unary("not", self.not_expr())
            self.depth -= 1
            return node
        return self.comparison()

    def comparison(self):
        left = self.additive()
        if self._is_op("<", "<=", ">", ">=", "==", "!="):
            op = self._next().value
            return Compare(op, left, self.additive())
        if self._is_kw("in"):
            self._next()
            return Compare("in", left, self.additive())
        if self._is_kw("not") and self.tokens[self.i + 1].kind == "kw" and self.tokens[self.i + 1].value == "in":
            self._next()
            self._next()
            return Compare("not in", left, self.additive())
        return left

    def additive(self):
        node = self.multiplicative()
        while self._is_op("+", "-"):
            op = self._next().value
            node = Binary(op, node, self.multiplicative())
        return node

    def multiplicative(self):
        node = self.unary()
        while self._is_op("*", "/", "%"):
            op = self._next().value
            node = Binary(op, node, self.unary())
        return node

    def unary(self):
        if self._is_op("-"):
            self._next()
            self._enter()
            node = Unary("-", self.unary())
            self.depth -= 1
            return node
        return self.postfix()

    def postfix(self):
        node = self.primary()
        while True:
            if self._is_op("."):
                self._next()
                tok = self._next()
                if tok.kind not in ("name", "kw"):
                    self.i -= 1
                    self._fail("Expected a field name after '.'")
                node = Attr(node, str(tok.value))
            elif self._is_op("["):
                self._next()
                idx = self.or_expr()
                self._expect_op("]")
                node = Index(node, idx)
            else:
                return node

    def primary(self):
        tok = self.cur
        if tok.kind == "number":
            self._next()
            return Const(tok.value)
        if tok.kind == "string":
            self._next()
            return Const(tok.value)
        if tok.kind == "kw":
            if tok.value in ("true", "True"):
                self._next()
                return Const(True)
            if tok.value in ("false", "False"):
                self._next()
                return Const(False)
            if tok.value in ("null", "None"):
                self._next()
                return Const(None)
            self._fail("Unexpected keyword")
        if tok.kind == "name":
            self._next()
            if self._is_op("("):
                if tok.value not in FUNCTIONS:
                    raise ExpressionSyntaxError(
                        f"Unknown function '{tok.value}'. Supported functions: {', '.join(sorted(FUNCTIONS))}",
                        detail=self.text,
                    )
                self._next()
                args = []
                self._enter()
                if not self._is_op(")"):
                    args.append(self.or_expr())
                    while self._is_op(","):
                        self._next()
                        args.append(self.or_expr())
                self.depth -= 1
                self._expect_op(")")
                return Call(tok.value, tuple(args))
            return Name(tok.value)
        if self._is_op("("):
            self._next()
            node = self.or_expr()
            self._expect_op(")")
            return node
        if self._is_op("["):
            self._next()
            items = []
            self._enter()
            if not self._is_op("]"):
                items.append(self.or_expr())
                while self._is_op(","):
                    self._next()
                    items.append(self.or_expr())
            self.depth -= 1
            self._expect_op("]")
            return ListLit(tuple(items))
        self._fail("Expected a value")


# ----------------------------------------------------------------------------
# Value helpers
# ----------------------------------------------------------------------------
def _type_name(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "text"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and not (isinstance(v, float) and math.isnan(v))


def _num(v: Any, what: str) -> float | int:
    if not _is_num(v):
        raise ExpressionError(f"{what} needs a number but got {_type_name(v)} ({preview(v)})")
    return v


def preview(v: Any, limit: int = 60) -> str:
    """Short, safe rendering of a value for error messages and traces."""
    if isinstance(v, str):
        s = repr(v if len(v) <= limit else v[: limit - 1] + "…")
    elif isinstance(v, list):
        s = f"[list of {len(v)}]"
    elif isinstance(v, dict):
        s = "{" + ", ".join(list(map(str, v))[:4]) + (", …" if len(v) > 4 else "") + "}"
    else:
        s = repr(v)
    return s


def format_value(v: Any) -> str:
    """Human-friendly text rendering used by templates and ``fmt``."""
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        if math.isnan(v):
            return "n/a"
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.2f}".rstrip("0").rstrip(".")
    if isinstance(v, (list, tuple)):
        return ", ".join(format_value(x) for x in v)
    if isinstance(v, dict):
        return ", ".join(f"{k}: {format_value(x)}" for k, x in v.items())
    return str(v)


def round_half_up(value: float | int, ndigits: int = 0) -> float | int:
    try:
        q = Decimal(1).scaleb(-ndigits)
        out = Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ExpressionError(f"Cannot round {preview(value)}") from exc
    return int(out) if ndigits <= 0 else float(out)


def norm_text(v: Any) -> str:
    """Lower-case, strip punctuation and collapse whitespace (``Men's  Belt!`` -> ``men s belt``)."""
    if v is None:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(v).lower())).strip()


def norm_code(v: Any) -> str:
    """Normalise an identifier: drop every non-alphanumeric character, lower-case."""
    if v is None:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(v).lower())


def is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "") or (isinstance(v, float) and math.isnan(v))


def _parse_date(v: Any, what: str) -> date:
    if isinstance(v, date):
        return v
    if not isinstance(v, str):
        raise ExpressionError(f"{what} needs an ISO date (YYYY-MM-DD) but got {_type_name(v)}")
    try:
        return date.fromisoformat(v.strip())
    except ValueError as exc:
        raise ExpressionError(f"{what} needs an ISO date (YYYY-MM-DD) but got {preview(v)}") from exc


# ----------------------------------------------------------------------------
# Functions (whitelist)
# ----------------------------------------------------------------------------
# Eager functions receive evaluated args. Lazy functions receive (evaluator, arg_nodes).
def _f_abs(x):
    return abs(_num(x, "abs()"))


def _f_round(x, n=0):
    return round_half_up(_num(x, "round()"), int(_num(n, "round() digits")))


def _f_ceil(x):
    return math.ceil(_num(x, "ceil()"))


def _f_floor(x):
    return math.floor(_num(x, "floor()"))


def _flatten_args(args, what):
    if len(args) == 1 and isinstance(args[0], list):
        args = tuple(args[0])
    if not args:
        raise ExpressionError(f"{what}() needs at least one value")
    return args


def _f_min(*args):
    vals = _flatten_args(args, "min")
    return min(_num(v, "min()") for v in vals)


def _f_max(*args):
    vals = _flatten_args(args, "max")
    return max(_num(v, "max()") for v in vals)


def _f_sum(xs):
    if not isinstance(xs, list):
        raise ExpressionError(f"sum() needs a list but got {_type_name(xs)}")
    return sum(_num(v, "sum()") for v in xs)


def _f_len(x):
    if isinstance(x, (list, str, dict)):
        return len(x)
    raise ExpressionError(f"len() needs a list, text or object but got {_type_name(x)}")


def _f_lower(x):
    return "" if x is None else str(x).lower()


def _f_upper(x):
    return "" if x is None else str(x).upper()


def _f_strip(x):
    return "" if x is None else str(x).strip()


def _f_str(x):
    return format_value(x)


def _f_contains(hay, needle):
    if isinstance(hay, (list, str)):
        return needle in hay
    if isinstance(hay, dict):
        return needle in hay
    raise ExpressionError(f"contains() needs a list, text or object but got {_type_name(hay)}")


def _f_startswith(s, prefix):
    return isinstance(s, str) and s.startswith(str(prefix))


def _f_intersect(a, b):
    if not isinstance(a, list) or not isinstance(b, list):
        raise ExpressionError("intersect() needs two lists")
    bn = {norm_text(x) for x in b}
    seen, out = set(), []
    for x in a:
        k = norm_text(x)
        if k in bn and k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _f_unique(a):
    if not isinstance(a, list):
        raise ExpressionError("unique() needs a list")
    out, seen = [], set()
    for x in a:
        k = repr(x)
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _f_first(a, default=None):
    if not isinstance(a, list):
        raise ExpressionError("first() needs a list")
    return a[0] if a else default


def _f_join(a, sep=", "):
    if not isinstance(a, list):
        raise ExpressionError("join() needs a list")
    return str(sep).join(format_value(x) for x in a)


def _f_concat(*lists):
    out: list = []
    for a in lists:
        if a is None:
            continue
        if not isinstance(a, list):
            raise ExpressionError(f"concat() needs lists but got {_type_name(a)}")
        out.extend(a)
    return out


def _f_get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    if obj is None:
        return default
    raise ExpressionError(f"get() needs an object but got {_type_name(obj)}")


def _f_fmt(template, *args):
    if not isinstance(template, str):
        raise ExpressionError("fmt() needs a text template as its first argument")
    parts = template.split("{}")
    if len(parts) - 1 != len(args):
        raise ExpressionError(f"fmt() template has {len(parts) - 1} placeholders but {len(args)} values were given")
    out = parts[0]
    for arg, tail in zip(args, parts[1:]):
        out += format_value(arg) + tail
    return out


def _f_pct_change(old, new):
    old, new = _num(old, "pct_change()"), _num(new, "pct_change()")
    if old == 0:
        raise ExpressionError("pct_change() cannot use a base value of 0")
    return (new - old) / old * 100


def _f_days_between(a, b):
    return (_parse_date(b, "days_between()") - _parse_date(a, "days_between()")).days


def _f_add_days(d, n):
    return (_parse_date(d, "add_days()") + timedelta(days=int(_num(n, "add_days()")))).isoformat()


def _f_is_blank(x):
    return is_blank(x)


def _f_number(x):
    if _is_num(x):
        return x
    if isinstance(x, str):
        try:
            s = x.strip().replace(",", "")
            return int(s) if re.fullmatch(r"-?\d+", s) else float(s)
        except ValueError:
            pass
    raise ExpressionError(f"number() cannot convert {preview(x)}")


# lazy ----------------------------------------------------------------------
def _lazy_iff(ev, args):
    if len(args) != 3:
        raise ExpressionError("iff() needs exactly 3 arguments: iff(condition, then, else)")
    cond = ev(args[0])
    if not isinstance(cond, bool):
        raise ExpressionError(f"iff() condition must be true/false but got {_type_name(cond)} ({preview(cond)})")
    return ev(args[1] if cond else args[2])


def _lazy_coalesce(ev, args):
    if not args:
        raise ExpressionError("coalesce() needs at least one argument")
    for a in args:
        try:
            v = ev(a)
        except MissingValueError:
            continue
        if not is_blank(v):
            return v
    return None


def _lazy_exists(ev, args):
    if len(args) != 1:
        raise ExpressionError("exists() needs exactly 1 argument")
    try:
        return ev(args[0]) is not None
    except MissingValueError:
        return False


EAGER_FUNCTIONS: dict[str, Callable] = {
    "abs": _f_abs, "round": _f_round, "ceil": _f_ceil, "floor": _f_floor,
    "min": _f_min, "max": _f_max, "sum": _f_sum, "len": _f_len,
    "lower": _f_lower, "upper": _f_upper, "strip": _f_strip, "str": _f_str,
    "norm_text": norm_text, "norm_code": norm_code, "contains": _f_contains,
    "startswith": _f_startswith, "intersect": _f_intersect, "unique": _f_unique,
    "first": _f_first, "join": _f_join, "concat": _f_concat, "get": _f_get, "fmt": _f_fmt,
    "pct_change": _f_pct_change, "days_between": _f_days_between,
    "add_days": _f_add_days, "is_blank": _f_is_blank, "number": _f_number,
}
LAZY_FUNCTIONS: dict[str, Callable] = {"iff": _lazy_iff, "coalesce": _lazy_coalesce, "exists": _lazy_exists}
FUNCTIONS = {**EAGER_FUNCTIONS, **LAZY_FUNCTIONS}

FUNCTION_DOCS: dict[str, str] = {
    "abs(x)": "absolute value",
    "round(x[, n])": "round half-up to n decimals (default 0)",
    "ceil(x) / floor(x)": "round up / down to an integer",
    "min(a, b, …) / max(…)": "smallest / largest number (or of one list)",
    "sum(list)": "sum of a list of numbers",
    "len(x)": "length of a list, text or object",
    "lower / upper / strip(text)": "text case and whitespace helpers",
    "str(x)": "business-friendly text rendering of a value",
    "norm_text(x)": "lower-case, punctuation removed, whitespace collapsed",
    "norm_code(x)": "identifier normalisation: only lower-case letters and digits",
    "contains(list|text, x)": "membership test (same as `x in list`)",
    "startswith(text, prefix)": "text prefix test",
    "intersect(a, b) / unique(a)": "list helpers (case-insensitive intersection / de-duplication)",
    "first(list[, default])": "first element or default",
    "join(list[, sep])": "join list items into text",
    "concat(list, list, …)": "concatenate lists (null lists are skipped)",
    "get(obj, key[, default])": "tolerant key lookup (no error when the key is absent)",
    "fmt(template, …)": "fill `{}` placeholders in order, e.g. fmt('{} units', 5)",
    "pct_change(old, new)": "(new - old) / old * 100",
    "days_between(a, b) / add_days(d, n)": "ISO-date arithmetic",
    "is_blank(x)": "true for null, empty or whitespace-only text",
    "number(x)": "convert numeric text such as '1,250' to a number",
    "iff(cond, a, b)": "lazy if/else (only the chosen branch is evaluated)",
    "coalesce(a, b, …)": "first value that is not blank (missing paths are skipped)",
    "exists(path)": "true when a path exists and is not null",
}

# ----------------------------------------------------------------------------
# Evaluation
# ----------------------------------------------------------------------------
def _path_text(node) -> str:
    if isinstance(node, Name):
        return node.name
    if isinstance(node, Attr):
        return f"{_path_text(node.obj)}.{node.key}"
    if isinstance(node, Index):
        inner = node.index.value if isinstance(node.index, Const) else "…"
        return f"{_path_text(node.obj)}[{inner!r}]" if isinstance(inner, str) else f"{_path_text(node.obj)}[{inner}]"
    return "<value>"


class _Evaluator:
    def __init__(self, scope: dict[str, Any]):
        self.scope = scope

    def ev(self, node) -> Any:
        t = type(node)
        if t is Const:
            return node.value
        if t is Name:
            if node.name in self.scope:
                return self.scope[node.name]
            raise MissingValueError(
                f"Unknown name '{node.name}'. Available: {', '.join(sorted(k for k in self.scope if not k.startswith('_'))) or 'none'}"
            )
        if t is Attr:
            obj = self.ev(node.obj)
            if isinstance(obj, dict):
                if node.key in obj:
                    return obj[node.key]
                raise MissingValueError(f"'{_path_text(node)}' does not exist (available: {', '.join(map(str, list(obj)[:12]))})")
            raise MissingValueError(f"Cannot read '{node.key}' from {_type_name(obj)} at '{_path_text(node.obj)}'")
        if t is Index:
            obj = self.ev(node.obj)
            idx = self.ev(node.index)
            if isinstance(obj, list) and isinstance(idx, int) and not isinstance(idx, bool):
                if -len(obj) <= idx < len(obj):
                    return obj[idx]
                raise MissingValueError(f"Index {idx} is out of range for '{_path_text(node.obj)}' (length {len(obj)})")
            if isinstance(obj, dict) and isinstance(idx, str):
                if idx in obj:
                    return obj[idx]
                raise MissingValueError(f"'{_path_text(node)}' does not exist")
            raise MissingValueError(f"Cannot index {_type_name(obj)} at '{_path_text(node.obj)}' with {preview(idx)}")
        if t is ListLit:
            if len(node.items) > MAX_LIST_SIZE:
                raise ExpressionError("List literal too large")
            return [self.ev(i) for i in node.items]
        if t is Unary:
            v = self.ev(node.operand)
            if node.op == "not":
                if not isinstance(v, bool):
                    raise ExpressionError(f"'not' needs true/false but got {_type_name(v)} ({preview(v)})")
                return not v
            return -_num(v, "Unary '-'")
        if t is BoolOp:
            for sub in node.values:
                v = self.ev(sub)
                if not isinstance(v, bool):
                    raise ExpressionError(f"'{node.op}' needs true/false operands but got {_type_name(v)} ({preview(v)})")
                if node.op == "and" and not v:
                    return False
                if node.op == "or" and v:
                    return True
            return node.op == "and"
        if t is Binary:
            return self._binary(node)
        if t is Compare:
            return self._compare(node)
        if t is Call:
            if node.func in LAZY_FUNCTIONS:
                return LAZY_FUNCTIONS[node.func](self.ev, node.args)
            args = [self.ev(a) for a in node.args]
            try:
                return EAGER_FUNCTIONS[node.func](*args)
            except TypeError as exc:
                raise ExpressionError(f"Wrong number of arguments for {node.func}(): {exc}") from exc
        raise ExpressionError(f"Unsupported expression node {t.__name__}")  # pragma: no cover

    def _binary(self, node: Binary):
        a, b = self.ev(node.left), self.ev(node.right)
        op = node.op
        if op == "+" and isinstance(a, str) and isinstance(b, str):
            return a + b
        label = f"'{op}'"
        a, b = _num(a, label), _num(b, label)
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if b == 0:
            raise ExpressionError("Division by zero")
        return a / b if op == "/" else a % b

    def _compare(self, node: Compare):
        a, b = self.ev(node.left), self.ev(node.right)
        op = node.op
        if op in ("==", "!="):
            eq = _equal(a, b)
            return eq if op == "==" else not eq
        if op in ("in", "not in"):
            if isinstance(b, (list, dict)):
                found = a in b if not isinstance(a, (list, dict)) else False
            elif isinstance(b, str) and isinstance(a, str):
                found = a in b
            else:
                raise ExpressionError(f"'{op}' needs a list, object or text on the right but got {_type_name(b)}")
            return found if op == "in" else not found
        if a is None or b is None:
            raise ExpressionError(
                f"Cannot compare with '{op}' because a value is missing (null): left={preview(a)}, right={preview(b)}"
            )
        if _is_num(a) and _is_num(b):
            pass
        elif isinstance(a, str) and isinstance(b, str):
            pass
        else:
            raise ExpressionError(f"Cannot compare {_type_name(a)} {preview(a)} with {_type_name(b)} {preview(b)} using '{op}'")
        return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


def _equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if _is_num(a) and _is_num(b):
        return a == b
    return a == b


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Ref:
    """A static reference found in an expression, e.g. ``steps.load.rows``."""

    segments: tuple  # static leading segments, e.g. ("steps", "load", "rows")

    @property
    def root(self) -> str:
        return self.segments[0]

    @property
    def dotted(self) -> str:
        out = self.segments[0]
        for s in self.segments[1:]:
            out += f"[{s}]" if isinstance(s, int) else f".{s}"
        return out


class Expression:
    """A parsed, reusable expression."""

    def __init__(self, text: str, node):
        self.text = text
        self.node = node

    def __repr__(self) -> str:  # pragma: no cover
        return f"Expression({self.text!r})"

    def evaluate(self, scope: dict[str, Any]) -> Any:
        try:
            return _Evaluator(scope).ev(self.node)
        except ExpressionError as exc:
            if exc.detail is None:
                exc.detail = self.text
            raise
        except RecursionError as exc:  # pragma: no cover - guarded by MAX_DEPTH
            raise ExpressionError("Expression too deeply nested") from exc

    def evaluate_bool(self, scope: dict[str, Any]) -> bool:
        value = self.evaluate(scope)
        if not isinstance(value, bool):
            raise ExpressionError(
                f"Condition must evaluate to true/false but gave {_type_name(value)} ({preview(value)})",
                detail=self.text,
            )
        return value

    def references(self) -> list[Ref]:
        refs: list[Ref] = []
        _collect_refs(self.node, refs)
        seen, out = set(), []
        for r in refs:
            if r.segments not in seen:
                seen.add(r.segments)
                out.append(r)
        return out

    def functions_used(self) -> set[str]:
        used: set[str] = set()
        _collect_calls(self.node, used)
        return used


def _static_chain(node) -> tuple | None:
    """Return the static leading segments of a name/attr/index chain."""
    if isinstance(node, Name):
        return (node.name,)
    if isinstance(node, Attr):
        base = _static_chain(node.obj)
        return None if base is None else base + (node.key,)
    if isinstance(node, Index):
        base = _static_chain(node.obj)
        if base is None:
            return None
        if isinstance(node.index, Const) and isinstance(node.index.value, (str, int)) and not isinstance(node.index.value, bool):
            return base + (node.index.value,)
        return base  # dynamic index: keep only the static prefix
    return None


def _collect_refs(node, out: list[Ref]) -> None:
    chain = _static_chain(node) if isinstance(node, (Name, Attr, Index)) else None
    if chain is not None:
        out.append(Ref(chain))
        # also descend into dynamic index expressions
        n = node
        while isinstance(n, (Attr, Index)):
            if isinstance(n, Index) and not isinstance(n.index, Const):
                _collect_refs(n.index, out)
            n = n.obj
        return
    for child in _children(node):
        _collect_refs(child, out)


def _collect_calls(node, used: set[str]) -> None:
    if isinstance(node, Call):
        used.add(node.func)
    for child in _children(node):
        _collect_calls(child, used)


def _children(node) -> Iterable:
    if isinstance(node, (Attr,)):
        return (node.obj,)
    if isinstance(node, Index):
        return (node.obj, node.index)
    if isinstance(node, Call):
        return node.args
    if isinstance(node, Unary):
        return (node.operand,)
    if isinstance(node, (Binary, Compare)):
        return (node.left, node.right)
    if isinstance(node, BoolOp):
        return node.values
    if isinstance(node, ListLit):
        return node.items
    return ()


@lru_cache(maxsize=2048)
def compile_expression(text: str) -> Expression:
    """Parse ``text`` into an :class:`Expression` (cached)."""
    if not isinstance(text, str) or not text.strip():
        raise ExpressionSyntaxError("Expression is empty")
    if len(text) > MAX_EXPRESSION_LENGTH:
        raise ExpressionSyntaxError(f"Expression is longer than {MAX_EXPRESSION_LENGTH} characters")
    return Expression(text, _Parser(text).parse())


def evaluate(text: str, scope: dict[str, Any]) -> Any:
    return compile_expression(text).evaluate(scope)


def evaluate_bool(text: str, scope: dict[str, Any]) -> bool:
    return compile_expression(text).evaluate_bool(scope)


def resolve_ref(ref: Ref, scope: dict[str, Any]) -> Any:
    """Resolve a static reference against ``scope`` (raises MissingValueError)."""
    node: Any = Name(ref.segments[0])
    for seg in ref.segments[1:]:
        node = Index(node, Const(seg)) if isinstance(seg, int) else Attr(node, seg)
    return _Evaluator(scope).ev(node)


def explain_values(expr: Expression | str, scope: dict[str, Any], *, limit: int = 8) -> dict[str, Any]:
    """Values of every referenced path, for the condition trace ("values used")."""
    expression = compile_expression(expr) if isinstance(expr, str) else expr
    values: dict[str, Any] = {}
    for ref in expression.references():
        if ref.root not in scope:
            continue
        try:
            values[ref.dotted] = _summarise(resolve_ref(ref, scope))
        except ExpressionError:
            values[ref.dotted] = "<missing>"
        if len(values) >= limit:
            break
    return values


def _summarise(v: Any) -> Any:
    if isinstance(v, str):
        return v if len(v) <= 80 else v[:79] + "…"
    if isinstance(v, list):
        return f"[list of {len(v)}]"
    if isinstance(v, dict):
        return "{object}"
    return v


# ----------------------------------------------------------------------------
# Templates
# ----------------------------------------------------------------------------
_TEMPLATE_RE = re.compile(r"\{\{(.+?)\}\}", re.DOTALL)


def has_template(s: Any) -> bool:
    return isinstance(s, str) and _TEMPLATE_RE.search(s) is not None


def template_expressions(template: str) -> list[str]:
    return [m.group(1).strip() for m in _TEMPLATE_RE.finditer(template)]


def render_template(template: str, scope: dict[str, Any]) -> str:
    """Replace each ``{{ expr }}`` with the business-friendly text of its value."""

    def repl(m: re.Match) -> str:
        return format_value(evaluate(m.group(1).strip(), scope))

    return _TEMPLATE_RE.sub(repl, template)


def is_expression_string(value: Any) -> bool:
    """True for strings written as ``=expression`` (typed expression)."""
    return isinstance(value, str) and value.startswith("=") and not value.startswith("==")
