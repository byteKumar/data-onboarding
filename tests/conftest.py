"""Test setup. No test may ever reach a paid API: every ANTHROPIC_* variable is removed
and LLM_PROVIDER is forced to 'none' before anything is imported."""

import os
import shutil
from pathlib import Path

import pytest

for _k in list(os.environ):
    if _k.startswith("ANTHROPIC_"):
        del os.environ[_k]
os.environ["LLM_PROVIDER"] = "none"

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def small_project(tmp_path_factory) -> Path:
    """A throwaway project root with ~600 customer records generated at a fixed seed."""
    from onboard.synth import generate

    root = tmp_path_factory.mktemp("proj")
    generate(root / "data", scale=0.03, seed=7)
    shutil.copy(ROOT / "sources.yaml", root / "sources.yaml")
    (root / "config").mkdir()
    return root


@pytest.fixture(scope="session")
def confirmed_project(small_project) -> Path:
    """small_project with every table's mapping confirmed (simulated human, from ground truth)."""
    import sys

    sys.path.insert(0, str(ROOT / "eval"))
    from seed_confirmed_mappings import seed

    seed(small_project, small_project / "sources.yaml", small_project / "data" / "ground_truth")
    return small_project
