"""Dependency wiring for the dev-only hypothesis dependency.

Validates: Requirements 24.1, 24.2, 24.3
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _install_requires_block(setup_text):
    match = re.search(r"install_requires\s*=\s*\[(.*?)\]", setup_text, re.S)
    assert match, "install_requires not found in setup.py"
    return match.group(1)


def test_hypothesis_in_dev_requirements():
    lines = [ln.strip().lower() for ln in _read("dev-requirements.txt").splitlines()]
    assert any(re.match(r"hypothesis\b", ln) for ln in lines)


def test_hypothesis_installed_in_every_unit_job():
    for rel in (".github/workflows/test.yml", ".github/workflows/publish.yml"):
        text = _read(rel)
        jobs = len(re.findall(r"pip install -e \.", text))
        installs = len(re.findall(r"pip install [^\n]*\bhypothesis\b", text))
        assert jobs >= 1, rel
        assert installs >= jobs, "%s: %d jobs, %d hypothesis installs" % (rel, jobs, installs)


def test_hypothesis_not_a_runtime_dependency():
    assert "hypothesis" not in _install_requires_block(_read("setup.py")).lower()
