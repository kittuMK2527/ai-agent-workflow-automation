"""Rule-based SEO tools: search-intent classification and category mapping.

Deliberately **not** an LLM: classification is a transparent lexicon lookup
(``data/reference/intent_lexicon.json``) so results are reproducible and each
row reports the signals that decided it. The workbook allows "LLM/classifier".
"""

from __future__ import annotations

from pydantic import Field

from ..condition_evaluator import norm_text
from ..errors import ToolInputError
from ..tool_registry import ToolContext, ToolRegistry, ToolSpec
from .common import In, Out, Row

INTENTS = ("informational", "commercial", "transactional", "navigational")


def _has(text: str, terms: list[str]) -> list[str]:
    padded = f" {text} "
    return [t for t in terms if f" {norm_text(t)} " in padded]


class KeywordClassifierIn(In):
    rows: list[Row]
    field: str = "keyword"


class KeywordClassifierOut(Out):
    rows: list[Row]
    count: int
    intent_counts: dict[str, int]
    defaulted_count: int


def keyword_classifier(p: KeywordClassifierIn, ctx: ToolContext) -> dict:
    lex = ctx.data.load_reference_json("intent_lexicon.json")
    if p.rows and p.field not in p.rows[0]:
        raise ToolInputError(f"Rows have no column '{p.field}'.", code="unknown_column")
    out: list[Row] = []
    counts = {i: 0 for i in INTENTS}
    defaulted = 0
    for row in p.rows:
        text = norm_text(row[p.field])
        words = text.split()
        nav = _has(text, lex["navigational"])
        info_start = words[0] in lex["informational_starts"] if words else False
        info_terms = _has(text, lex["informational_terms"])
        trans = _has(text, lex["transactional"])
        comm = _has(text, lex["commercial"])
        if nav:
            intent, signals, conf = "navigational", nav, "high"
        elif info_start or info_terms:
            intent, signals, conf = "informational", info_terms or [words[0]], "high"
        elif trans:
            intent, signals, conf = "transactional", trans, "high"
        elif comm:
            intent, signals, conf = "commercial", comm, "medium"
        else:
            intent, signals, conf = "commercial", [], "low"
            defaulted += 1
        counts[intent] += 1
        out.append({**row, "intent": intent, "intent_signals": ", ".join(signals) or "no explicit intent words (defaulted)", "intent_confidence": conf})
    return {"rows": out, "count": len(out), "intent_counts": counts, "defaulted_count": defaulted}


class CategoryMapperIn(In):
    rows: list[Row]
    categories: list[Row]
    field: str = "keyword"
    min_score: int = Field(default=1, ge=1)


class CategoryMapperOut(Out):
    rows: list[Row]
    count: int
    mapped_count: int
    unmapped_count: int


def category_mapper(p: CategoryMapperIn, ctx: ToolContext) -> dict:
    for need in ("category", "target_page", "terms"):
        if p.categories and need not in p.categories[0]:
            raise ToolInputError(f"The category file needs a '{need}' column.", code="missing_columns")
    cats = [
        (c["category"], c["target_page"], [norm_text(t) for t in str(c.get("terms") or "").split(";") if norm_text(t)])
        for c in p.categories
    ]
    out: list[Row] = []
    mapped = 0
    for row in p.rows:
        padded = f" {norm_text(row[p.field])} "
        best: tuple[int, str, str, list[str]] | None = None
        for name, page, terms in cats:
            hits = [t for t in terms if f" {t} " in padded]
            score = sum(len(t.split()) for t in hits)
            if score >= p.min_score and (best is None or score > best[0]):
                best = (score, name, page, hits)
        if best:
            mapped += 1
            out.append({**row, "category": best[1], "target_page": best[2], "matched_terms": ", ".join(best[3])})
        else:
            out.append({**row, "category": None, "target_page": None, "matched_terms": ""})
    return {"rows": out, "count": len(out), "mapped_count": mapped, "unmapped_count": len(out) - mapped}


def register(reg: ToolRegistry) -> None:
    reg.register(ToolSpec("keyword_classifier", "Classify keyword search intent (informational / commercial / transactional / navigational) with a transparent rule lexicon.",
                          KeywordClassifierIn, KeywordClassifierOut, keyword_classifier, kind="computation"))
    reg.register(ToolSpec("category_mapper", "Map keywords to the best-matching product category and target page using category terms.",
                          CategoryMapperIn, CategoryMapperOut, category_mapper, kind="computation"))
