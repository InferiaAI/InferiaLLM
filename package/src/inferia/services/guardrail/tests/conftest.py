"""Shared test fixtures for guardrail service tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_llama_guard():
    """Mock LlamaGuardProvider."""
    provider = AsyncMock()
    provider.name = "llama-guard"
    return provider


@pytest.fixture
def mock_llm_guard():
    """Mock LLMGuardProvider."""
    provider = AsyncMock()
    provider.name = "llm-guard"
    return provider


@pytest.fixture
def mock_lakera():
    """Mock LakeraProvider."""
    provider = AsyncMock()
    provider.name = "lakera-guard"
    return provider
