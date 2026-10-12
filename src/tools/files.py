"""Data-source tools: CSV reader, Excel/CSV parser, keyword reader, product
data reader and the dataset exporter.

All of them read through :class:`DataAccess` (sandboxed to fixtures/uploads).
The data behind them is local fixture data (see ``data/fixtures/README.md``),
so these tools are marked ``local_data`` - they are *not* external APIs.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from pathlib import Path
from typing import Any

from pydantic import Field

from ..condition_evaluator import is_blank, norm_code, norm_text
from ..errors import ToolInputError
from ..tool_registry import ToolContext, ToolRegistry, ToolSpec
from .common import In, Out, Row, plural, public_columns, to_snake
from .data_access import to_number


# --------------------------------------------------------------- csv_reader
class CsvReaderIn(In):
    source: str = Field(description="File name inside the fixtures/uploads folders.")
    numeric_columns: list[str] = Field(default_factory=list, description="Columns to convert to numbers.")
    required_columns: list[str] = Field(default_factory=list, description="Columns that must exist (after snake_case).")


class TableOut(Out):
    rows: list[Row]
    columns: list[str]
    row_count: int
    source: str
    warnings: list[str] = Field(default_factory=list)


def csv_reader(p: CsvReaderIn, ctx: ToolContext) -> dict:
    headers, rows, meta = ctx.data.read_table(p.source)
    warnings = list(meta["warnings"])
    mapping = {h: to_snake(h) for h in headers}
    snake_headers = list(mapping.values())
    missing = [c for c in p.required_columns + p.numeric_columns if c not in snake_headers]
    if missing:
        raise ToolInputError(
            f"'{meta['file']}' is missing required column(s): {', '.join(sorted(set(missing)))}. "
            f"Columns found: {', '.join(snake_headers)}.",
            code="missing_columns",
        )
    out: list[Row] = []
    bad: dict[str, int] = {}
    for r in rows:
        new = {mapping[h]: r[h] for h in headers}
        new["_source_row"] = r["_source_row"]  # lets data-quality messages say "row 17 of your file"
        for col in p.numeric_columns:
            raw = new[col]
            if raw is None:
                continue
            num = to_number(raw)
            if num is None:
                bad[col] = bad.get(col, 0) + 1
            new[col] = num
        out.append(new)
    for col, n in bad.items():
        warnings.append(f"Column '{col}': {plural(n, 'non-numeric value')} treated as missing.")
    return {"rows": out, "columns": snake_headers, "row_count": len(out), "source": meta["file"], "warnings": warnings}


# -------------------------------------------------------------- file_parser
class FileParserIn(In):
    source: str
    sheet: str | None = None


class FileParserOut(TableOut):
    format: str
    sheet: str | None = None


def file_parser(p: FileParserIn, ctx: ToolContext) -> dict:
    headers, rows, meta = ctx.data.read_table(p.source, sheet=p.sheet)
    return {
        "rows": rows,  # raw header names and raw text values; `_source_row` keeps the file row number
        "columns": headers,
        "row_count": len(rows),
        "source": meta["file"],
        "format": meta["format"],
        "sheet": meta.get("sheet"),
        "warnings": meta["warnings"],
    }


# ----------------------------------------------------------- keyword_reader
_KEYWORD_HEADERS = {"keyword", "keywords", "query", "queries", "searchterm", "searchquery", "term", "phrase"}
_VOLUME_HEADERS = {"searchvolume", "volume", "monthlysearches", "searches", "avgmonthlysearches"}


class KeywordReaderIn(In):
    source: str | None = Field(default=None, description="CSV/XLSX file with a keyword column.")
    keywords: list[str] | None = Field(default=None, description="Keywords typed directly into the request.")


class KeywordReaderOut(TableOut):
    input_mode: str


def keyword_reader(p: KeywordReaderIn, ctx: ToolContext) -> dict:
    warnings: list[str] = []
    if p.keywords:
        rows = [{"keyword": k.strip()} for k in p.keywords if isinstance(k, str) and k.strip()]
        mode, src, columns = "inline", "request", ["keyword"]
    elif p.source:
        headers, raw_rows, meta = ctx.data.read_table(p.source)
        warnings.extend(meta["warnings"])
        norm = {h: re.sub(r"[^a-z0-9]", "", h.lower()) for h in headers}
        kw_col = next((h for h in headers if norm[h] in _KEYWORD_HEADERS), None)
        if kw_col is None and len(headers) == 1:
            kw_col = headers[0]
        if kw_col is None:
            raise ToolInputError(
                f"Could not find a keyword column in '{meta['file']}'. Columns found: {', '.join(headers)}. "
                "Name the column 'keyword'.",
                code="keyword_column_missing",
            )
        vol_col = next((h for h in headers if norm[h] in _VOLUME_HEADERS), None)
        rows = []
        dropped = 0
        for r in raw_rows:
            kw = r.get(kw_col)
            if is_blank(kw):
                dropped += 1
                continue
            row: Row = {"keyword": str(kw).strip()}
            if vol_col:
                row["search_volume"] = to_number(r.get(vol_col))
            rows.append(row)
        if dropped:
            warnings.append(f"{plural(dropped, 'row')} without a keyword were skipped.")
        mode, src = "file", meta["file"]
        columns = ["keyword"] + (["search_volume"] if vol_col else [])
    else:
        raise ToolInputError("No keywords were provided. Attach a keyword CSV or type the keywords.", code="no_keywords")
    if not rows:
        raise ToolInputError("The keyword list is empty.", code="no_keywords")
    return {"rows": rows, "columns": columns, "row_count": len(rows), "source": src, "warnings": warnings, "input_mode": mode}


# --------------------------------------------------------- product_data_reader
#: Filler words people (and language models) put around a collection name: "the Autumn 2026
#: collection" means the collection called "Autumn 2026". Only used as a second chance below.
_SELECTOR_FILLER = frozenset({"the", "our", "new", "collection", "range", "line", "products", "product", "items", "item"})


def _core_selector(text: str) -> str:
    """``norm_text`` form without filler words; empty when nothing but filler is left."""
    return " ".join(w for w in text.split() if w not in _SELECTOR_FILLER)


class ProductReaderIn(In):
    selectors: list[str] = Field(description="SKUs, product names or a collection name.")
    catalog: str = "catalog.csv"


class ProductReaderOut(Out):
    products: list[Row]
    count: int
    matched_selectors: list[str]
    unmatched_selectors: list[str]
    source: str


def product_data_reader(p: ProductReaderIn, ctx: ToolContext) -> dict:
    _, rows, meta = ctx.data.read_table(p.catalog)
    cleaned = []
    for r in rows:
        r = {to_snake(k): v for k, v in r.items() if not k.startswith("_")}
        if "price" in r:
            r["price"] = to_number(r["price"])
        cleaned.append(r)
    found: dict[int, Row] = {}
    matched, unmatched = [], []
    for sel in p.selectors:
        sel_code, sel_text = norm_code(sel), norm_text(sel)
        if not sel_text:
            continue
        hits = [i for i, r in enumerate(cleaned) if norm_code(r.get("sku")) == sel_code and sel_code]
        if not hits:
            hits = [i for i, r in enumerate(cleaned) if norm_text(r.get("collection")) == sel_text]
        if not hits:
            hits = [i for i, r in enumerate(cleaned) if sel_text in norm_text(r.get("product_name"))]
        core = _core_selector(sel_text)
        if not hits and core and core != sel_text:  # "Autumn 2026 collection" -> "Autumn 2026"
            hits = [i for i, r in enumerate(cleaned) if norm_text(r.get("collection")) == core]
            hits = hits or [i for i, r in enumerate(cleaned) if core in norm_text(r.get("product_name"))]
        if hits:
            matched.append(sel)
            for i in hits:
                found.setdefault(i, cleaned[i])
        else:
            unmatched.append(sel)
    products = [found[i] for i in sorted(found)]
    return {
        "products": products,
        "count": len(products),
        "matched_selectors": matched,
        "unmatched_selectors": unmatched,
        "source": meta["file"],
    }


# ---------------------------------------------------------- dataset_exporter
class ExporterIn(In):
    rows: list[Row]
    name: str = Field(description="Base file name (letters, digits, '-' and '_' only).")
    columns: list[str] | None = None


class ExporterOut(Out):
    path: str
    file_name: str
    row_count: int
    columns: list[str]
    bytes: int
    sha256: str


def _csv_safe(value: Any) -> Any:
    """Neutralise spreadsheet formula injection (cells starting with = + @ or -text)."""
    if isinstance(value, str) and value and (value[0] in "=+@" or (value[0] == "-" and to_number(value) is None)):
        return "'" + value
    return value


def dataset_exporter(p: ExporterIn, ctx: ToolContext) -> dict:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", p.name).strip("_") or "export"
    columns = p.columns or public_columns(p.rows)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    for r in p.rows:
        writer.writerow(["" if r.get(c) is None else _csv_safe(r.get(c)) for c in columns])
    data = buf.getvalue().encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    # Content-addressed name: exporting the same data twice writes the same file
    # (idempotent), so a retry can never create duplicates.
    out_dir = Path(ctx.settings.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{safe}_{digest[:8]}.csv"
    if not target.exists():
        target.write_bytes(data)
    try:
        shown = str(target.relative_to(Path(ctx.settings.output_dir).parent))
    except ValueError:
        shown = str(target)
    return {
        "path": shown,
        "file_name": target.name,
        "row_count": len(p.rows),
        "columns": columns,
        "bytes": len(data),
        "sha256": digest,
    }


def register(reg: ToolRegistry) -> None:
    reg.register(ToolSpec("csv_reader", "Read a CSV file into rows (headers normalised to snake_case, optional numeric conversion).",
                          CsvReaderIn, TableOut, csv_reader, kind="local_data"))
    reg.register(ToolSpec("file_parser", "Parse a CSV or XLSX file keeping raw headers and raw text values.",
                          FileParserIn, FileParserOut, file_parser, kind="local_data"))
    reg.register(ToolSpec("keyword_reader", "Read keywords from a CSV/XLSX file or an inline list.",
                          KeywordReaderIn, KeywordReaderOut, keyword_reader, kind="local_data"))
    reg.register(ToolSpec("product_data_reader", "Look up products in the catalog by SKU, name or collection.",
                          ProductReaderIn, ProductReaderOut, product_data_reader, kind="local_data"))
    reg.register(ToolSpec("dataset_exporter", "Write rows to a CSV file in the outputs folder (content-addressed name, so repeating it is safe).",
                          ExporterIn, ExporterOut, dataset_exporter, kind="file_output", side_effect=True, idempotent=True))
