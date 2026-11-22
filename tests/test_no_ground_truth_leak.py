"""Pipeline code must never read the hidden ground truth. Only eval/ may.

synth.py is the one exception: it writes the ground truth."""

from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "onboard"


def test_pipeline_never_mentions_ground_truth():
    offenders = [p.name for p in SRC.rglob("*.py") if p.name != "synth.py" and "ground_truth" in p.read_text()]
    assert offenders == []
