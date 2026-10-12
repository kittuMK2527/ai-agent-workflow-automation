"""Safe expression evaluator: operators, functions, error handling, security."""

import pytest

from src.condition_evaluator import (
    compile_expression,
    evaluate,
    evaluate_bool,
    explain_values,
    render_template,
    template_expressions,
    is_expression_string,
    format_value,
)
from src.errors import ExpressionError, ExpressionSyntaxError, MissingValueError


SCOPE = {
    "inputs": {"threshold": 10, "name": "Belt", "none": None, "tags": ["a", "B"]},
    "row": {"current_stock": 4, "minimum_stock": 10, "sku": "SKU-1", "Unit Price": 3.5, "blank": "  "},
    "steps": {"load": {"rows": [{"sku": "A"}, {"sku": "B"}], "count": 2}},
}


@pytest.mark.parametrize(
    "text,expected",
    [
        ("row.current_stock < row.minimum_stock", True),
        ("row.current_stock >= row.minimum_stock", False),
        ("row.current_stock == 4", True),
        ("row.current_stock != 4", False),
        ("1 + 2 * 3", 7),
        ("(1 + 2) * 3", 9),
        ("10 / 4", 2.5),
        ("10 % 4", 2),
        ("-row.current_stock", -4),
        ("'a' + 'b'", "ab"),
        ("row.current_stock < 5 and row.minimum_stock > 5", True),
        ("row.current_stock > 5 or row.minimum_stock > 5", True),
        ("not (row.current_stock > 5)", True),
        ("row.sku in ['SKU-1', 'SKU-2']", True),
        ("row.sku not in ['SKU-1']", False),
        ("'elt' in inputs.name", True),
        ("steps.load.rows[1].sku", "B"),
        ("steps.load.rows[-1].sku", "B"),
        ("row['Unit Price'] * 2", 7.0),
        ("len(steps.load.rows) == steps.load.count", True),
        ("abs(-3)", 3),
        ("round(2.5)", 3),            # half-up, not banker's rounding
        ("round(2.675, 2)", 2.68),
        ("ceil(2.1)", 3),
        ("floor(2.9)", 2),
        ("min(3, 1, 2)", 1),
        ("max([3, 9, 2])", 9),
        ("sum([1, 2, 3])", 6),
        ("is_blank(row.blank)", True),
        ("is_blank(inputs.none)", True),
        ("coalesce(inputs.none, row.sku)", "SKU-1"),
        ("coalesce(inputs.missing_key, 7)", 7),          # missing paths are skipped
        ("exists(inputs.missing_key)", False),
        ("exists(inputs.name)", True),
        ("iff(row.current_stock < 5, 'low', 'ok')", "low"),
        ("iff(true, 1, 1 / 0)", 1),                      # lazy: bad branch never evaluated
        ("norm_code(' SKU-1 ')", "sku1"),
        ("norm_text(\"Men's  Belt!\")", "men s belt"),
        ("fmt('{} of {}', 3, 'x')", "3 of x"),
        ("join(inputs.tags, '|')", "a|B"),
        ("intersect(['Python','sql'], ['SQL','go'])", ["sql"]),
        ("pct_change(100, 112.5)", 12.5),
        ("days_between('2026-11-01', '2026-11-30')", 29),
        ("add_days('2026-11-01', 3)", "2026-11-04"),
        ("number('1,250')", 1250),
        ("get(row, 'nope', 0)", 0),
        ("1 == 1.0", True),
        ("true == 1", False),                            # bool is not a number
        ("null == null", True),
        ("inputs.none == null", True),
        ("'2026-11-01' < '2026-11-30'", True),           # ISO dates order lexicographically
    ],
)
def test_expression_values(text, expected):
    assert evaluate(text, SCOPE) == expected


def test_evaluate_bool_requires_a_boolean():
    assert evaluate_bool("row.current_stock < 5", SCOPE) is True
    with pytest.raises(ExpressionError, match="true/false"):
        evaluate_bool("row.current_stock", SCOPE)


@pytest.mark.parametrize(
    "text,match",
    [
        ("inputs.none < 5", "missing"),                  # ordering with null never silently false
        ("5 < inputs.none", "missing"),
        ("'a' < 5", "Cannot compare"),
        ("1 / 0", "Division by zero"),
        ("1 + 'a'", "needs a number"),
        ("inputs.none + 1", "needs a number"),
        ("not 5", "true/false"),
        ("5 and true", "true/false"),
        ("iff(5, 1, 2)", "true/false"),
        ("min()", "at least one"),
        ("abs('x')", "needs a number"),
        ("days_between('nope', '2026-01-01')", "ISO date"),
        ("fmt('{} {}', 1)", "placeholders"),
        ("pct_change(0, 5)", "base value of 0"),
    ],
)
def test_runtime_errors_are_explicit(text, match):
    with pytest.raises(ExpressionError, match=match):
        evaluate(text, SCOPE)


@pytest.mark.parametrize(
    "text",
    [
        "row.nope",
        "steps.load.rows[5]",
        "unknown_root.x",
        "inputs.tags.x",
    ],
)
def test_missing_paths_raise_missing_value(text):
    with pytest.raises(MissingValueError):
        evaluate(text, SCOPE)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "1 +",
        "row.current_stock = 5",       # assignment is not allowed
        "(1 + 2",
        "foo(1)",                       # unknown function
        "__import__('os')",
        "open('/etc/passwd')",
        "eval('1')",
        "row.__class__",
        "[x for x in y]",
        "lambda x: x",
        "1; 2",
        "a ** 2",
        "'unterminated",
        "x" * 900,
    ],
)
def test_syntax_errors_and_unsafe_constructs_are_rejected(text):
    # Either a syntax error at parse time (unknown function / syntax) or a
    # missing-value error at evaluation time. Nothing is ever executed.
    with pytest.raises(ExpressionError):
        evaluate(text, SCOPE)


def test_dunder_access_cannot_reach_python_objects():
    # Attribute access only reads dict keys, so this is simply a missing key.
    with pytest.raises(MissingValueError):
        evaluate("inputs.__class__", SCOPE)
    with pytest.raises(MissingValueError):
        evaluate("inputs.name.__class__", SCOPE)


def test_deep_nesting_is_bounded():
    with pytest.raises(ExpressionSyntaxError, match="nested too deeply"):
        compile_expression("(" * 60 + "1" + ")" * 60)


def test_references_are_extracted_statically():
    expr = compile_expression("steps.load.rows[0].sku == inputs.name and row.current_stock < vars.min")
    dotted = {r.dotted for r in expr.references()}
    assert {"steps.load.rows[0].sku", "inputs.name", "row.current_stock", "vars.min"} <= dotted
    assert compile_expression("iff(a, 1, 2)").functions_used() == {"iff"}


def test_explain_values_reports_values_and_missing():
    vals = explain_values("row.current_stock < row.minimum_stock and inputs.nope == 1", SCOPE)
    assert vals["row.current_stock"] == 4
    assert vals["row.minimum_stock"] == 10
    assert vals["inputs.nope"] == "<missing>"


def test_templates():
    assert template_expressions("A {{ x }} B {{ y + 1 }}") == ["x", "y + 1"]
    out = render_template("{{ inputs.name }} has {{ row.current_stock }} left ({{ row.current_stock / 8 }})", SCOPE)
    assert out == "Belt has 4 left (0.5)"
    assert render_template("{{ inputs.none }}", SCOPE) == "n/a"


def test_expression_string_marker():
    assert is_expression_string("=steps.a.b")
    assert not is_expression_string("==")
    assert not is_expression_string("plain")
    assert not is_expression_string(5)


def test_format_value():
    assert format_value(3.0) == "3"
    assert format_value(3.14159) == "3.14"
    assert format_value(True) == "yes"
    assert format_value(None) == "n/a"
    assert format_value([1, "a"]) == "1, a"
