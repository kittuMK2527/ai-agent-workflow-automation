"""Sandboxed file access shared by every file-reading tool.

User-influenced file names (an uploaded vendor file, a ``source`` parameter an
LLM extracted) must never be able to read arbitrary files, so every path is
resolved against an explicit allow-list of roots (fixtures + uploads). Symlinks
and ``..`` segments cannot escape because the *resolved* path is checked.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import Settings
from ..errors import ToolInputError

ALLOWED_SUFFIXES = {".csv", ".xlsx", ".json", ".tsv", ".txt"}


class DataAccess:
    def __init__(self, settings: Settings, extra_roots: tuple[Path, ...] = ()):
        self.settings = settings
        self.roots: tuple[Path, ...] = tuple(Path(r).resolve() for r in (*settings.data_roots, *extra_roots))

    # ------------------------------------------------------------- resolution
    def resolve(self, name: str) -> Path:
        """Return the real path for ``name`` (a file name or a path inside an allowed root)."""
        if not isinstance(name, str) or not name.strip():
            raise ToolInputError("No data file was specified.", code="file_not_specified")
        raw = name.strip()
        if "\x00" in raw:
            raise ToolInputError("Invalid file name.", code="file_invalid")
        candidates: list[Path] = []
        p = Path(raw)
        if p.is_absolute():
            candidates.append(p)
        else:
            candidates.extend(root / p for root in self.roots)
        for cand in candidates:
            try:
                real = cand.resolve()
            except OSError:
                continue
            if not any(real == root or real.is_relative_to(root) for root in self.roots):
                continue
            if real.is_file():
                if real.suffix.lower() not in ALLOWED_SUFFIXES:
                    raise ToolInputError(
                        f"Unsupported file type '{real.suffix}'. Supported: CSV, XLSX.", code="file_type_unsupported"
                    )
                if real.stat().st_size > self.settings.max_upload_bytes * 4:
                    raise ToolInputError("The file is too large to process.", code="file_too_large")
                return real
        available = self.available_files()
        hint = f" Available sample files: {', '.join(available[:8])}." if available else ""
        raise ToolInputError(f"File '{Path(raw).name}' was not found.{hint}", code="file_not_found")

    def available_files(self) -> list[str]:
        names: list[str] = []
        for root in self.roots:
            if root.is_dir():
                names.extend(sorted(f.name for f in root.iterdir() if f.is_file() and f.suffix.lower() in {".csv", ".xlsx"}))
        return names

    def reference_path(self, name: str) -> Path:
        """Path to a lexicon/reference JSON in ``data/reference`` (not user-controllable)."""
        path = (self.settings.reference_dir / name).resolve()
        if not path.is_relative_to(self.settings.reference_dir.resolve()) or not path.is_file():
            raise ToolInputError(f"Reference data '{name}' is missing.", code="reference_missing")
        return path

    def load_reference_json(self, name: str) -> Any:
        try:
            return json.loads(self.reference_path(name).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ToolInputError(f"Reference data '{name}' is not valid JSON.", detail=str(exc)) from exc

    # ---------------------------------------------------------------- reading
    def read_table(self, name: str, *, sheet: str | None = None) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
        """Read a CSV/XLSX file as ``(headers, rows, meta)`` with every value as text.

        Values are kept as raw text (blank -> ``None``) so identifiers such as
        ``00123`` are never turned into numbers. Tools coerce explicitly.
        """
        path = self.resolve(name)
        meta: dict[str, Any] = {"file": path.name, "format": path.suffix.lower().lstrip("."), "warnings": []}
        try:
            if path.suffix.lower() == ".xlsx":
                xl = pd.ExcelFile(path, engine="openpyxl")
                chosen = sheet if sheet and sheet in xl.sheet_names else xl.sheet_names[0]
                if sheet and sheet not in xl.sheet_names:
                    meta["warnings"].append(f"Sheet '{sheet}' not found; used '{chosen}'.")
                meta["sheet"] = chosen
                df = xl.parse(chosen, header=None, dtype=object, keep_default_na=False)
                header_idx = _first_nonblank_row(df)
                if header_idx is None:
                    raise ToolInputError("The spreadsheet is empty.", code="file_empty")
                headers = [str(h).strip() for h in df.iloc[header_idx].tolist()]
                body = df.iloc[header_idx + 1 :].values.tolist()
                line_numbers = [header_idx + 2 + i for i in range(len(body))]
            else:
                text, enc = _decode(path.read_bytes())
                if enc != "utf-8":
                    meta["warnings"].append(f"File was not UTF-8; decoded as {enc}.")
                delimiter = _sniff_delimiter(text)
                reader = csv.reader(io.StringIO(text), delimiter=delimiter)
                all_rows = []
                for r in reader:
                    all_rows.append((reader.line_num, r))
                idx = next((i for i, (_, r) in enumerate(all_rows) if any(c.strip() for c in r)), None)
                if idx is None:
                    raise ToolInputError("The file is empty.", code="file_empty")
                headers = [h.strip() for h in all_rows[idx][1]]
                body = [r for _, r in all_rows[idx + 1 :]]
                line_numbers = [ln for ln, _ in all_rows[idx + 1 :]]
        except ToolInputError:
            raise
        except Exception as exc:
            raise ToolInputError(
                f"Could not read '{path.name}'. Is it a valid CSV or XLSX file?", detail=f"{type(exc).__name__}: {exc}", code="file_unreadable"
            ) from exc

        headers = _dedupe_headers([h if h else f"column_{i + 1}" for i, h in enumerate(headers)], meta["warnings"])
        if len(body) > self.settings.max_table_rows:
            raise ToolInputError(
                f"'{path.name}' has more than {self.settings.max_table_rows:,} rows, which is over the limit.", code="file_too_large"
            )
        rows: list[dict[str, Any]] = []
        for raw, line_no in zip(body, line_numbers):
            cells = [("" if c is None else str(c).strip()) for c in raw]
            if not any(cells):
                continue
            cells = (cells + [""] * len(headers))[: len(headers)]
            row = {h: (c if c != "" else None) for h, c in zip(headers, cells)}
            row["_source_row"] = line_no  # 1-based line/row number in the source file
            rows.append(row)
        meta["source_rows"] = len(rows)
        return headers, rows, meta


def _first_nonblank_row(df: pd.DataFrame) -> int | None:
    for i in range(len(df)):
        if any(str(v).strip() for v in df.iloc[i].tolist()):
            return i
    return None


def _decode(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8"), "utf-8"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def _sniff_delimiter(text: str) -> str:
    # Decide from the header line only: data cells may legitimately contain ';' lists.
    header = next((ln for ln in text.splitlines() if ln.strip()), "")
    counts = {d: header.count(d) for d in [",", ";", "\t", "|"]}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def _dedupe_headers(headers: list[str], warnings: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for h in headers:
        key = h.lower()
        if key in seen:
            seen[key] += 1
            new = f"{h}_{seen[key]}"
            warnings.append(f"Duplicate column '{h}' renamed to '{new}'.")
            out.append(new)
        else:
            seen[key] = 1
            out.append(h)
    return out


def to_number(value: Any) -> float | int | None:
    """Parse numeric text (``'1,250'``, ``'$12.50'``, ``'10%'``); ``None`` if not numeric."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    text = re.sub(r"[,$€£%\s]", "", str(value))
    if not re.fullmatch(r"-?\d+(\.\d+)?", text):
        return None
    return int(text) if re.fullmatch(r"-?\d+", text) else float(text)
