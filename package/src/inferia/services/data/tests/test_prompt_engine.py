"""Tests for prompt engine — complex logic layer."""

import pytest

from inferia.services.data.prompt_engine import PromptEngine


@pytest.fixture
def engine():
    return PromptEngine()


class TestTokenBudget:
    """Token budget checking."""

    def test_under_budget_returns_true(self, engine):
        # "Hello world" is ~2 tokens
        assert engine.check_token_budget("Hello world", budget=100) is True

    def test_over_budget_returns_false(self, engine):
        # Long text with budget of 1 token
        assert engine.check_token_budget("This is a very long text that exceeds the budget", budget=1) is False

    def test_unknown_model_falls_back_to_default(self, engine):
        """Unknown model name uses cl100k_base fallback."""
        # Should not raise, should still count tokens
        result = engine.check_token_budget("Hello", budget=100, model_name="nonexistent-model-xyz")
        assert result is True

    def test_empty_text_returns_true(self, engine):
        assert engine.check_token_budget("", budget=0) is True
        assert engine.check_token_budget(None, budget=0) is True

    def test_count_tokens(self, engine):
        count = engine.count_tokens("Hello world")
        assert isinstance(count, int)
        assert count > 0
