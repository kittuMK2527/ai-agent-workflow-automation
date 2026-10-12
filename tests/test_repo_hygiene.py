"""Repository hygiene: no secrets, required deliverables, and docs that are generated from (and must match) the code."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys

import pytest

from src.config import PROJECT_ROOT, REQUIRED_WORKFLOW_IDS

ROOT = PROJECT_ROOT
SKIP_DIRS = {".venv", "venv", ".git", ".pytest_cache", "__pycache__", "outputs", "uploads", ".mypy_cache", "node_modules"}


def repo_files():
    for p in ROOT.rglob("*"):
        if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS) and p.suffix not in {".xlsx", ".pyc", ".png", ".jpg"}:
            yield p


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- secrets
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"OPENAI_API_KEY\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}"),
]


def test_no_secret_like_strings_in_the_repository():
    hits = []
    for p in repo_files():
        if "tests" in p.relative_to(ROOT).parts:  # tests contain deliberately fake keys to prove redaction
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                hits.append(f"{p.relative_to(ROOT)}: {pat.pattern}")
    assert not hits, hits


def test_env_example_lists_names_but_no_values():
    text = (ROOT / ".env.example").read_text()
    assert re.search(r"^OPENAI_API_KEY=\s*$", text, re.M), "OPENAI_API_KEY must be present and empty"
    assert re.search(r"^OPENAI_MODEL=", text, re.M) and re.search(r"^LLM_MODE=", text, re.M)


def test_gitignore_protects_secrets_and_generated_files():
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    for needed in (".env", ".venv/", "__pycache__/", "outputs/*", "uploads/*", ".pytest_cache/", ".DS_Store"):
        assert needed in ignored, needed
    assert "!.env.example" in ignored


def test_no_env_file_is_tracked_by_git():
    if not (ROOT / ".git").exists():
        pytest.skip("not a git repository yet")
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    assert not [t for t in tracked if t == ".env" or t.startswith(".env.") and t != ".env.example"]
    assert not [t for t in tracked if t.startswith((".venv/", "outputs/", "uploads/")) and not t.endswith(".gitkeep")]


def test_requirements_are_bounded_and_explicit():
    for name in ("requirements.txt", "requirements-dev.txt"):
        for line in (ROOT / name).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "-r")):
                continue
            assert ">=" in line and "<" in line, f"{name}: '{line}' must have a lower and an upper bound"


# --------------------------------------------------------- deliverables
REQUIRED_FILES = [
    "app.py", "requirements.txt", ".env.example", ".gitignore", "README.md", "pytest.ini",
    "data/workflows.xlsx", "data/normalized_workflows.json", "data/fixtures/README.md",
    *[f"data/specs/{w}.yaml" for w in REQUIRED_WORKFLOW_IDS],
    "src/config.py", "src/models.py", "src/excel_loader.py", "src/workflow_registry.py", "src/agent.py", "src/router.py",
    "src/execution_engine.py", "src/condition_evaluator.py", "src/tool_registry.py", "src/result_formatter.py", "src/tools/__init__.py",
    "tests/test_excel_loader.py", "tests/test_workflow_registry.py", "tests/test_router.py", "tests/test_execution_engine.py",
    "tests/test_conditions.py", "tests/test_tools.py", "tests/test_workflows.py",
    "examples/sample_requests.md", "examples/expected_outputs.md", "examples/extension_wf011/workflows_extension.xlsx",
    "examples/extension_wf011/specs/WF011.yaml",
    "docs/workflow_analysis.md", "docs/architecture.md", "docs/technical_decisions.md", "docs/loom_demo_script.md",
    "docs/validation_report.md", "docs/test_report.md",
    "scripts/validate_workflows.py", "scripts/run_smoke_tests.py", "scripts/verify_live_llm.py",
    "AI_Agent_Workflow_Assessment (1).xlsx",
]


@pytest.mark.parametrize("path", REQUIRED_FILES)
def test_required_deliverable_exists(path):
    assert (ROOT / path).is_file(), path


README_SECTIONS = [
    "Features", "Architecture", "Technology choices", "Prerequisites", "Installation", "Environment variables", "Run the application",
    "Run the tests", "How the Excel workflows are processed", "How the agent selects a workflow", "How tool calling works",
    "How conditions and branching work", "How errors and retries are handled", "How to add an 11th workflow",
    "Sample requests and expected results", "Simulation versus real API behaviour", "Security considerations", "Known limitations",
    "Troubleshooting",
]


def test_readme_has_every_required_section_and_is_honest_about_the_live_llm():
    text = (ROOT / "README.md").read_text()
    headings = re.findall(r"^## \d*\.?\s*(.+)$", text, re.M)
    for section in README_SECTIONS:
        assert any(h.strip().lower().startswith(section.lower()) for h in headings), section
    assert "could not be exercised" in text and "SIMULATED" in text.upper()


def test_documentation_covers_every_workflow():
    analysis = (ROOT / "docs" / "workflow_analysis.md").read_text()
    for wid in REQUIRED_WORKFLOW_IDS:
        assert f"### {wid} ·" in analysis
    assert "examples" in (ROOT / "docs" / "loom_demo_script.md").read_text().lower()
    samples = (ROOT / "examples" / "sample_requests.md").read_text()
    for wid in REQUIRED_WORKFLOW_IDS:
        assert f"## {wid} ·" in samples


def test_no_invented_urls_for_github_or_loom():
    for rel in ("README.md", "docs/loom_demo_script.md", "docs/test_report.md"):
        text = (ROOT / rel).read_text()
        assert "loom.com/share" not in text and not re.search(r"github\.com/[\w\-]+/[\w\-]+", text), rel


# ------------------------------------------- generated docs match the code
def test_workflow_analysis_matches_the_catalog(registry):
    mod = load_script("generate_workflow_analysis")
    assert (ROOT / "docs" / "workflow_analysis.md").read_text() == mod.build_document(registry), \
        "run `python scripts/generate_workflow_analysis.py`"


def test_validation_report_matches_the_catalog(registry):
    mod = load_script("validate_workflows")
    assert (ROOT / "docs" / "validation_report.md").read_text() == mod.build_markdown(registry) + "\n", \
        "run `python scripts/validate_workflows.py --write`"


def test_example_docs_match_real_executions(settings, tools, registry, tmp_path):
    from src.agent import Agent
    mod = load_script("run_smoke_tests")
    agent = Agent(settings.with_overrides(output_dir=tmp_path / "outputs"), tools=tools, registry=registry, llm=None, audit=False)
    sample, expected = mod.build_documents(agent)
    assert (ROOT / "examples" / "sample_requests.md").read_text() == sample, "run `python scripts/run_smoke_tests.py --write-examples`"
    assert (ROOT / "examples" / "expected_outputs.md").read_text() == expected, "run `python scripts/run_smoke_tests.py --write-examples`"


def test_generated_fixtures_are_reproducible(tmp_path, monkeypatch):
    mod = load_script("generate_fixtures")
    monkeypatch.setattr(mod, "FIXTURES", tmp_path)
    mod.write_logs()
    assert (tmp_path / "workflow_execution_logs.csv").read_bytes() == (ROOT / "data" / "fixtures" / "workflow_execution_logs.csv").read_bytes()


def test_scripts_run_and_report_status():
    out = subprocess.run([sys.executable, "scripts/validate_workflows.py"], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0 and "OK - 10/10" in out.stdout
    live = subprocess.run([sys.executable, "scripts/verify_live_llm.py"], cwd=ROOT, capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "OPENAI_API_KEY": "", "PYTHONPATH": str(ROOT)})
    assert live.returncode == 2 and "NOT verified" in live.stdout  # without a key it must never look like a pass
