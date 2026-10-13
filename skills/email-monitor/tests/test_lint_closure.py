"""Require repaired lint behavior while retaining the original headroom tests."""
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "skills" / "email-monitor" / "scripts"))

import em_lint_rules as rules  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "email_lint_closure_fixtures", ROOT / "tools" / "make_fixtures.py"
)
_generator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_generator)
CASES = _generator.lint_closure_cases()


@pytest.mark.parametrize("case", [c for c in CASES["required"] if c["id"] == "rule_of_three"], ids=lambda case: case["id"])
def test_original_headroom_is_required(case):
    violations = rules.lint(case["text"], case["profile"], config=CASES["config"])
    assert any(case["diagnostic"] in violation for violation in violations), violations
