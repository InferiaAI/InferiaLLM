"""Shared fixtures for unified_config tests.

Test fixtures (yaml files) live under fixtures/ next to this conftest.
"""
import os
from pathlib import Path
from typing import Iterator
import pytest


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Absolute path to the fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def clean_env(monkeypatch) -> Iterator[None]:
    """Strip INFERIA_CONFIG and any leaked yaml-relevant vars for isolation."""
    for k in list(os.environ):
        if k in {"INFERIA_CONFIG"}:
            monkeypatch.delenv(k, raising=False)
    yield
