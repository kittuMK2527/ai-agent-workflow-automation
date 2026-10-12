"""Matching tools: pairwise record matching with text similarity, grouping of
matches (duplicate detection), the task analyzer and the report builder."""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Literal

from pydantic import Field

from ..condition_evaluator import format_value, is_blank, norm_code, norm_text
from ..errors import ToolInputError
from ..tool_registry import ToolContext, ToolRegistry, ToolSpec
from .common import In, Out, Row

MAX_COMPARE_ROWS = 2000


def text_similarity(a: Any, b: Any) -> float:
    """0..1 similarity robust to word order, punctuation and small spelling differences."""
    x, y = norm_text(a), norm_text(b)
    if not x or not y:
        return 0.0
    plain = SequenceMatcher(None, x, y).ratio()
    sx, sy = " ".join(sorted(x.split())), " ".join(sorted(y.split()))
    return max(plain, SequenceMatcher(None, sx, sy).ratio())


# --------------------------------------------------------------- record_matcher
class FieldSpec(In):
    name: str
    method: Literal["text", "exact"] = "text"
    weight: float = Field(default=1.0, gt=0)


class RecordMatcherIn(In):
    rows: list[Row]
    mode: Literal["exact", "similar"]
    exact_field: str | None = Field(default=None, description="mode=exact: rows with the same normalised value are definite duplicates")
    fields: list[FieldSpec] = Field(default_factory=list, description="mode=similar: fields compared and their weights")
    blockers: list[str] = Field(default_factory=list, description="mode=similar: if both rows have different values here, they are variants, not duplicates")
    threshold: float = Field(default=0.85, ge=0, le=1)
    exclude_pairs: list[dict[str, Any]] = Field(default_factory=list)
    id_field: str = "sku"


class MatchPair(Out):
    left_index: int
    right_index: int
    left_id: Any
    right_id: Any
    match_type: Literal["exact", "similar"]
    score: float
    matching_fields: list[str]
    differing_fields: list[str]


class RecordMatcherOut(Out):
    pairs: list[MatchPair]
    pair_count: int
    compared_pairs: int
    blocked_pairs: int


def record_matcher(p: RecordMatcherIn, ctx: ToolContext) -> dict:
    n = len(p.rows)
    if n > MAX_COMPARE_ROWS:
        raise ToolInputError(f"Too many rows to compare pairwise ({n}); the limit is {MAX_COMPARE_ROWS}.", code="too_many_rows")
    excluded = {(int(e["left_index"]), int(e["right_index"])) for e in p.exclude_pairs if "left_index" in e and "right_index" in e}
    pairs: list[dict] = []
    compared = blocked = 0

    if p.mode == "exact":
        if not p.exact_field:
            raise ToolInputError("mode=exact needs `exact_field`.")
        if p.rows and p.exact_field not in p.rows[0]:
            raise ToolInputError(f"Rows have no column '{p.exact_field}'.", code="unknown_column")
        buckets: dict[str, list[int]] = {}
        for i, r in enumerate(p.rows):
            key = norm_code(r.get(p.exact_field))
            if key:
                buckets.setdefault(key, []).append(i)
        for idxs in buckets.values():
            for a in range(len(idxs)):
                for b in range(a + 1, len(idxs)):
                    i, j = idxs[a], idxs[b]
                    compared += 1
                    pairs.append({"left_index": i, "right_index": j, "left_id": p.rows[i].get(p.id_field),
                                  "right_id": p.rows[j].get(p.id_field), "match_type": "exact", "score": 1.0,
                                  "matching_fields": [p.exact_field], "differing_fields": []})
        return {"pairs": pairs, "pair_count": len(pairs), "compared_pairs": compared, "blocked_pairs": 0}

    if not p.fields:
        raise ToolInputError("mode=similar needs at least one entry in `fields`.")
    for i in range(n):
        for j in range(i + 1, n):
            if (i, j) in excluded:
                continue
            compared += 1
            a, b = p.rows[i], p.rows[j]
            if any(not is_blank(a.get(f)) and not is_blank(b.get(f)) and norm_text(a.get(f)) != norm_text(b.get(f)) for f in p.blockers):
                blocked += 1
                continue
            total = weight_sum = 0.0
            matching: list[str] = []
            differing: list[str] = []
            for f in p.fields:
                va, vb = a.get(f.name), b.get(f.name)
                if is_blank(va) or is_blank(vb):
                    continue  # unknown, not evidence either way
                s = text_similarity(va, vb) if f.method == "text" else float(norm_text(va) == norm_text(vb))
                total += s * f.weight
                weight_sum += f.weight
                (matching if s >= 0.9 else differing).append(f.name)
            if weight_sum == 0:
                continue
            score = round(total / weight_sum, 3)
            if score >= p.threshold:
                pairs.append({"left_index": i, "right_index": j, "left_id": a.get(p.id_field), "right_id": b.get(p.id_field),
                              "match_type": "similar", "score": score, "matching_fields": matching, "differing_fields": differing})
    pairs.sort(key=lambda x: -x["score"])
    return {"pairs": pairs, "pair_count": len(pairs), "compared_pairs": compared, "blocked_pairs": blocked}


# ---------------------------------------------------------------- group_matches
class GroupMatchesIn(In):
    rows: list[Row]
    pairs: list[dict[str, Any]]
    display_fields: list[str] = Field(default_factory=lambda: ["sku", "product_name"])


class MatchGroup(Out):
    group_id: str
    match_type: Literal["definite", "possible"]
    size: int
    members: list[Row]
    members_text: str
    max_score: float
    min_score: float
    matching_fields: list[str]


class GroupMatchesOut(Out):
    groups: list[MatchGroup]
    total_groups: int
    definite_count: int
    possible_count: int


def _components(pairs: list[dict]) -> list[tuple[set[int], list[dict]]]:
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for pr in pairs:
        parent[find(pr["left_index"])] = find(pr["right_index"])
    comps: dict[int, tuple[set[int], list[dict]]] = {}
    for pr in pairs:
        root = find(pr["left_index"])
        members, edges = comps.setdefault(root, (set(), []))
        members.update((pr["left_index"], pr["right_index"]))
        edges.append(pr)
    return sorted(comps.values(), key=lambda c: min(c[0]))


def group_matches(p: GroupMatchesIn, ctx: ToolContext) -> dict:
    groups: list[dict] = []
    counters = {"definite": 0, "possible": 0}
    for kind, label in (("exact", "definite"), ("similar", "possible")):
        subset = [pr for pr in p.pairs if pr.get("match_type") == kind]
        for members, edges in _components(subset):
            counters[label] += 1
            common = set(edges[0]["matching_fields"])
            for e in edges[1:]:
                common &= set(e["matching_fields"])
            scores = [e["score"] for e in edges]
            groups.append({
                "group_id": f"{'D' if label == 'definite' else 'P'}{counters[label]}",
                "match_type": label,
                "size": len(members),
                "members": [{f: p.rows[i].get(f) for f in p.display_fields} for i in sorted(members)],
                "members_text": " | ".join(" ".join(str(p.rows[i].get(f)) for f in p.display_fields[:2] if p.rows[i].get(f) is not None) for i in sorted(members)),
                "max_score": max(scores), "min_score": min(scores),
                "matching_fields": sorted(common),
            })
    return {"groups": groups, "total_groups": len(groups),
            "definite_count": counters["definite"], "possible_count": counters["possible"]}


# ------------------------------------------------------------------ task_analyzer
class TaskAnalyzerIn(In):
    task_description: str
    explicit_skills: list[str] = Field(default_factory=list)


class TaskAnalyzerOut(Out):
    required_skills: list[str]
    matched_terms: dict[str, list[str]]
    source: Literal["explicit", "lexicon", "none"]
    note: str


def task_analyzer(p: TaskAnalyzerIn, ctx: ToolContext) -> dict:
    if p.explicit_skills:
        skills = sorted({s.strip().lower() for s in p.explicit_skills if s.strip()})
        return {"required_skills": skills, "matched_terms": {}, "source": "explicit",
                "note": "Skills were supplied by the requester."}
    lexicon = {k: v for k, v in ctx.data.load_reference_json("skill_lexicon.json").items() if not k.startswith("_")}
    text = f" {norm_text(p.task_description)} "
    matched: dict[str, list[str]] = {}
    for skill, terms in lexicon.items():
        hits = [t for t in terms if f" {norm_text(t)} " in text]
        if hits:
            matched[skill] = hits
    # 'django' implies the more general 'python' only if the task says python; keep literal matches only.
    if not matched:
        return {"required_skills": [], "matched_terms": {}, "source": "none",
                "note": "No known skills were recognised in the task description."}
    return {"required_skills": sorted(matched), "matched_terms": matched, "source": "lexicon",
            "note": "Skills were detected by keyword matching against data/reference/skill_lexicon.json (not by an LLM)."}


# ------------------------------------------------------------------ report_builder
class SectionIn(In):
    heading: str
    body: Any = None


class ReportBuilderIn(In):
    title: str
    sections: list[SectionIn]


class ReportBuilderOut(Out):
    title: str
    text: str
    section_count: int


def _render_body(body: Any) -> str:
    if body is None:
        return "n/a"
    if isinstance(body, list):
        if not body:
            return "- (none)"
        return "\n".join(f"- {format_value(b)}" for b in body)
    if isinstance(body, dict):
        return "\n".join(f"- {k}: {format_value(v)}" for k, v in body.items())
    return format_value(body)


def report_builder(p: ReportBuilderIn, ctx: ToolContext) -> dict:
    parts = [f"# {p.title}"]
    for s in p.sections:
        parts.append(f"## {s.heading}\n{_render_body(s.body)}")
    return {"title": p.title, "text": "\n\n".join(parts), "section_count": len(p.sections)}


def register(reg: ToolRegistry) -> None:
    reg.register(ToolSpec("record_matcher", "Pairwise record matching: exact normalised keys or weighted text similarity with variant blockers.",
                          RecordMatcherIn, RecordMatcherOut, record_matcher, kind="computation"))
    reg.register(ToolSpec("group_matches", "Group matched pairs into duplicate groups (definite vs possible).",
                          GroupMatchesIn, GroupMatchesOut, group_matches, kind="computation"))
    reg.register(ToolSpec("task_analyzer", "Detect required skills in a task description (keyword lexicon, not an LLM).",
                          TaskAnalyzerIn, TaskAnalyzerOut, task_analyzer, kind="computation"))
    reg.register(ToolSpec("report_builder", "Assemble a titled, sectioned text report from already-computed values.",
                          ReportBuilderIn, ReportBuilderOut, report_builder, kind="computation", core=True))
