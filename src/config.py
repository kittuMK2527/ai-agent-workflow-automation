"""Application configuration, read from environment variables (and ``.env``)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: The assignment defines exactly these ten workflows. The catalog is only
#: reported as healthy when every one of them is registered and valid.
REQUIRED_WORKFLOW_IDS: tuple[str, ...] = tuple(f"WF{n:03d}" for n in range(1, 11))

LLM_MODES = ("auto", "llm", "deterministic")
DEFAULT_MODEL = "gpt-4o-mini"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _env_path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    if not raw:
        return default
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (PROJECT_ROOT / p)


@dataclass(frozen=True)
class Settings:
    """Immutable settings object. Build with :meth:`Settings.from_env`."""

    openai_api_key: str = field(default="", repr=False)
    openai_model: str = DEFAULT_MODEL
    openai_base_url: str = ""
    openai_timeout_seconds: float = 30.0
    openai_max_retries: int = 2
    llm_mode: str = "auto"  # auto | llm | deterministic

    workbook_path: Path = PROJECT_ROOT / "data" / "workflows.xlsx"
    specs_dir: Path = PROJECT_ROOT / "data" / "specs"
    fixtures_dir: Path = PROJECT_ROOT / "data" / "fixtures"
    reference_dir: Path = PROJECT_ROOT / "data" / "reference"
    output_dir: Path = PROJECT_ROOT / "outputs"
    upload_dir: Path = PROJECT_ROOT / "uploads"
    #: Extra catalog directories (each holds ``*.xlsx`` and ``specs/``). Used for
    #: the demo "11th workflow" extension; empty by default.
    extra_catalog_dirs: tuple[Path, ...] = ()

    max_upload_bytes: int = 5 * 1024 * 1024
    max_table_rows: int = 50_000
    retry_backoff_scale: float = 1.0  # tests set 0 to avoid sleeping
    log_level: str = "INFO"

    # ------------------------------------------------------------------ props
    @property
    def has_api_key(self) -> bool:
        return bool(self.openai_api_key.strip())

    @property
    def llm_enabled(self) -> bool:
        """True when this configuration is allowed to call the LLM."""
        return self.llm_mode != "deterministic" and self.has_api_key

    @property
    def data_roots(self) -> tuple[Path, ...]:
        """Directories user-facing tools may read data files from."""
        return (self.fixtures_dir, self.upload_dir)

    # ---------------------------------------------------------------- factory
    @classmethod
    def from_env(cls, *, load_dotenv_file: bool = True, **overrides) -> "Settings":
        if load_dotenv_file:
            load_dotenv(PROJECT_ROOT / ".env", override=False)
        mode = (os.getenv("LLM_MODE") or "auto").strip().lower()
        if mode not in LLM_MODES:
            mode = "auto"
        extra = tuple(
            _env_path_value(p)
            for p in (os.getenv("EXTRA_CATALOG_DIRS") or "").split(os.pathsep)
            if p.strip()
        )
        base = cls(
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            openai_model=(os.getenv("OPENAI_MODEL") or DEFAULT_MODEL).strip(),
            openai_base_url=(os.getenv("OPENAI_BASE_URL") or "").strip(),
            openai_timeout_seconds=_env_float("OPENAI_TIMEOUT_SECONDS", 30.0),
            openai_max_retries=int(_env_float("OPENAI_MAX_RETRIES", 2)),
            llm_mode=mode,
            workbook_path=_env_path("WORKFLOW_WORKBOOK", PROJECT_ROOT / "data" / "workflows.xlsx"),
            specs_dir=_env_path("WORKFLOW_SPECS_DIR", PROJECT_ROOT / "data" / "specs"),
            fixtures_dir=_env_path("FIXTURES_DIR", PROJECT_ROOT / "data" / "fixtures"),
            reference_dir=_env_path("REFERENCE_DIR", PROJECT_ROOT / "data" / "reference"),
            output_dir=_env_path("OUTPUT_DIR", PROJECT_ROOT / "outputs"),
            upload_dir=_env_path("UPLOAD_DIR", PROJECT_ROOT / "uploads"),
            extra_catalog_dirs=extra,
            log_level=(os.getenv("LOG_LEVEL") or "INFO").upper(),
        )
        return base.with_overrides(**overrides) if overrides else base

    def with_overrides(self, **changes) -> "Settings":
        from dataclasses import replace

        return replace(self, **changes)

    # ------------------------------------------------------------- redaction
    def public_summary(self) -> dict:
        """Safe-to-display configuration summary (never contains the key)."""
        return {
            "llm_mode": self.llm_mode,
            "model": self.openai_model,
            "api_key_configured": self.has_api_key,
            "llm_enabled": self.llm_enabled,
            "workbook": str(self.workbook_path.relative_to(PROJECT_ROOT))
            if self.workbook_path.is_relative_to(PROJECT_ROOT)
            else str(self.workbook_path),
        }


def _env_path_value(raw: str) -> Path:
    p = Path(raw.strip()).expanduser()
    return p if p.is_absolute() else (PROJECT_ROOT / p)
