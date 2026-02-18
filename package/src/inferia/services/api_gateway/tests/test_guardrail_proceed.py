
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from guardrail.engine import GuardrailEngine
from guardrail.models import GuardrailResult, Violation

@pytest.mark.asyncio
async def test_proceed_on_violation():
    # Setup
    engine = GuardrailEngine()
    
    # Mock provider
    mock_provider = AsyncMock()
    mock_provider.name = "mock-provider"
    
    # Violation result
    violation_result = GuardrailResult(
        is_valid=False,
        sanitized_text="bad input",
        violations=[Violation(scanner="test", violation_type="toxicity", score=0.9, details="Toxic content")]
    )
    mock_provider.scan_input.return_value = violation_result
    
    engine.providers = {"mock-provider": mock_provider}
    engine.settings.default_guardrail_engine = "mock-provider"
    
    # Test Case 1: proceed_on_violation = False (default)
    config = {"proceed_on_violation": False, "guardrail_engine": "mock-provider"}
    result = await engine.scan_input("bad input", config=config)
    
    assert result.is_valid == False
    assert result.sanitized_text == "bad input"
    
    # Test Case 2: proceed_on_violation = True
    #Reset mock return since it might be modified in place? No model is mutable but return value instance might be.
    # Better to return new instance
    mock_provider.scan_input.return_value = GuardrailResult(
        is_valid=False,
        sanitized_text="bad input",
        violations=[Violation(scanner="test", violation_type="toxicity", score=0.9, details="Toxic content")]
    )
    
    config_proceed = {"proceed_on_violation": True, "guardrail_engine": "mock-provider"}
    result_proceed = await engine.scan_input("bad input", config=config_proceed)
    
    assert result_proceed.is_valid == True
    assert "User Configured to Proceed" in result_proceed.sanitized_text
    assert "proceed_on_violation_warning" in result_proceed.actions_taken
