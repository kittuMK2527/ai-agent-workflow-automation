"""Shared fixtures.

Tests never read OPENAI_API_KEY from the environment and never call the network:
LLM behaviour is exercised through :class:`tests.fakes.FakeLLM`.
"""

from __future__ import annotations

import shutil

import pytest

from src.agent import Agent
from src.config import PROJECT_ROOT, Settings
from src.tools import build_default_registry
from src.workflow_registry import WorkflowRegistry

EXTENSION_DIR = PROJECT_ROOT / "examples" / "extension_wf011"


@pytest.fixture(scope="session")
def tools():
    return build_default_registry()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings.from_env(load_dotenv_file=False).with_overrides(
        openai_api_key="",
        llm_mode="deterministic",
        output_dir=tmp_path / "outputs",
        upload_dir=tmp_path / "uploads",
        retry_backoff_scale=0,
    )


@pytest.fixture
def registry(settings, tools) -> WorkflowRegistry:
    reg = WorkflowRegistry(settings, tools)
    reg.load()
    return reg


@pytest.fixture
def agent(settings, tools, registry) -> Agent:
    return Agent(settings, registry=registry, tools=tools, llm=None, audit=False, sleep=lambda s: None)


@pytest.fixture
def catalog_copy(tmp_path, settings):
    """A writable copy of the workbook + specs so tests can break them on purpose."""
    specs = tmp_path / "specs"
    shutil.copytree(settings.specs_dir, specs)
    book = tmp_path / "workflows.xlsx"
    shutil.copy(settings.workbook_path, book)
    return settings.with_overrides(specs_dir=specs, workbook_path=book)


def edit_spec(settings: Settings, workflow_id: str, old: str, new: str) -> None:
    p = settings.specs_dir / f"{workflow_id}.yaml"
    text = p.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not found in {p.name}"
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
