"""Test setup: build a throwaway copy of the repo layout using FAKE receipts and price tags only."""

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(REPO / "scripts"))  # so tests can `import validate`, etc.

TODAY = "2026-11-09"  # fixed "today" so stale markers don't change over time


@pytest.fixture
def root(tmp_path):
    """A temp folder laid out like the repo, filled with the fake fixtures."""
    (tmp_path / "data" / "receipts").mkdir(parents=True)
    shutil.copytree(REPO / "config", tmp_path / "config")
    shutil.copytree(REPO / "schema", tmp_path / "schema")
    shutil.copy(FIXTURES / "products.json", tmp_path / "data" / "products.json")
    shutil.copy(FIXTURES / "aliases.json", tmp_path / "data" / "aliases.json")
    for f in (FIXTURES / "receipts").glob("*.json"):
        shutil.copy(f, tmp_path / "data" / "receipts" / f.name)
    (tmp_path / "data" / "price_tags").mkdir()
    for f in (FIXTURES / "price_tags").glob("*.json"):
        shutil.copy(f, tmp_path / "data" / "price_tags" / f.name)
    return tmp_path
