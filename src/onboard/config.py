"""Paths, fixed constants and env-driven settings.

Everything that changes behaviour lives here so it is easy to point at in a demo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Fixed FX rates (to USD). Fixed on purpose: the pipeline must be reproducible,
# and a live FX feed is a separate integration the customer would own.
FX_TO_USD = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27}

# LLM settings. Prices are per 1M tokens and only used for the cost estimate
# and for reporting spend from response.usage.
DEFAULT_LLM_MODEL = "claude-haiku-5-5"
LLM_PRICE_IN_PER_M = 0.10
LLM_PRICE_OUT_PER_M = 0.50

# "Today" for the at-risk rules. Fixed so the dashboard is reproducible;
# the synthetic data ends on this date.
AS_OF_DATE = "2025-06-30"


def project_root() -> Path:
    env = os.environ.get("ONBOARD_HOME")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


@dataclass
class Settings:
    root: Path = field(default_factory=project_root)

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def db_path(self) -> Path:
        return Path(os.environ.get("ONBOARD_DB", self.data_dir / "onboard.duckdb"))

    @property
    def mapping_store_dir(self) -> Path:
        return self.data_dir / "mapping_store"

    @property
    def suggestions_dir(self) -> Path:
        return self.data_dir / "suggestions"

    @property
    def manifest_path(self) -> Path:
        return self.root / "sources.yaml"

    @property
    def er_model_path(self) -> Path:
        return self.root / "config" / "er_model.json"


def llm_provider() -> str:
    """none | fake | anthropic. Default none: rules only, no LLM, no spend."""
    return os.environ.get("LLM_PROVIDER", "none").strip().lower()


def llm_model() -> str:
    return os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL)


# Fallback segment for customers with no CRM record, from their billing plan.
# ASSUMPTION to confirm with the customer's finance team; used only when CRM has no segment,
# and the golden record says so in segment_source='plan_rule'.
PLAN_SEGMENT = {"ENT": "Enterprise", "ENT-XL": "Enterprise", "PRO": "Mid-Market", "BAS": "SMB", "STARTER": "SMB"}
