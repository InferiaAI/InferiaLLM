"""
Mock orchestration layer responses.
Simulates responses from the compute and orchestration layers.
"""

import asyncio
import time
from typing import List
from datetime import datetime

from models import (
    InferenceRequest,
    InferenceResponse,
    Message,
    Choice,
    Usage,
    ModelInfo,
    ModelsListResponse
)
from config import settings


class MockOrchestrationService:
    """Mock orchestration service to simulate compute layer responses."""
    
    def __init__(self):
        self.mock_delay_ms = settings.mock_response_delay_ms
    
    async def _simulate_delay(self):
        """Simulate network/processing delay."""
        if self.mock_delay_ms > 0:
            await asyncio.sleep(self.mock_delay_ms / 1000.0)
    
    def _get_mock_response_content(self, model: str, messages: List[Message]) -> str:
        """Generate mock response content based on model and input."""
        user_message = next((m.content for m in reversed(messages) if m.role == "user"), "")
        
        # Different response styles for different models
        if "gpt-4" in model.lower():
            return f"[GPT-4 Mock Response] I understand you said: '{user_message}'. This is a simulated response from the GPT-4 model through the filtration layer."
        elif "gpt-3.5" in model.lower():
            return f"[GPT-3.5 Mock Response] You asked: '{user_message}'. This is a simulated response for testing the filtration layer."
        elif "claude" in model.lower():
            return f"[Claude-3 Mock Response] Regarding your query: '{user_message}'. This demonstrates the filtration layer's model routing capabilities."
        elif "llama" in model.lower():
            return f"[Llama-3 Mock Response] Processing: '{user_message}'. This shows the filtration layer working with open-source models."
        elif "mistral" in model.lower():
            return f"[Mistral Mock Response] Input received: '{user_message}'. This validates the filtration layer's RBAC and routing logic."
        else:
            return f"[Unknown Model Mock Response] Query: '{user_message}'. This is a generic mock response."
    
    def _calculate_mock_usage(self, messages: List[Message], response_content: str) -> Usage:
        """Calculate mock token usage."""
        # Simple estimation: ~4 characters per token
        prompt_chars = sum(len(m.content) for m in messages)
        completion_chars = len(response_content)
        
        return Usage(
            prompt_tokens=prompt_chars // 4,
            completion_tokens=completion_chars // 4,
            total_tokens=(prompt_chars + completion_chars) // 4
        )
    
    async def generate_completion(
        self,
        request: InferenceRequest,
        request_id: str
    ) -> InferenceResponse:
        """Generate mock completion response."""
        # Simulate processing delay
        start_time = time.time()
        await self._simulate_delay()
        
        # Generate mock response content
        response_content = self._get_mock_response_content(request.model, request.messages)
        
        # Calculate mock usage
        usage = self._calculate_mock_usage(request.messages, response_content)
        
        # Create response message
        assistant_message = Message(
            role="assistant",
            content=response_content
        )
        
        # Create choice
        choice = Choice(
            index=0,
            message=assistant_message,
            finish_reason="stop"
        )
        
        # Calculate processing time
        processing_time_ms = (time.time() - start_time) * 1000
        
        # Create response
        response = InferenceResponse(
            model=request.model,
            choices=[choice],
            usage=usage,
            request_id=request_id,
            processing_time_ms=processing_time_ms
        )
        
        return response
    
    def get_available_models(self) -> ModelsListResponse:
        """Get list of available mock models."""
        mock_models = [
            ModelInfo(
                id="gpt-4",
                created=1677649963,
                owned_by="openai",
                description="Most capable GPT-4 model (mock)"
            ),
            ModelInfo(
                id="gpt-4-turbo",
                created=1677649963,
                owned_by="openai",
                description="Fast GPT-4 variant (mock)"
            ),
            ModelInfo(
                id="gpt-3.5-turbo",
                created=1677649963,
                owned_by="openai",
                description="Fast and efficient model (mock)"
            ),
            ModelInfo(
                id="claude-3-opus",
                created=1677649963,
                owned_by="anthropic",
                description="Most capable Claude model (mock)"
            ),
            ModelInfo(
                id="claude-3-sonnet",
                created=1677649963,
                owned_by="anthropic",
                description="Balanced Claude model (mock)"
            ),
            ModelInfo(
                id="llama-3-70b",
                created=1677649963,
                owned_by="meta",
                description="Large Llama 3 model (mock)"
            ),
            ModelInfo(
                id="llama-3-8b",
                created=1677649963,
                owned_by="meta",
                description="Efficient Llama 3 model (mock)"
            ),
            ModelInfo(
                id="mistral-7b",
                created=1677649963,
                owned_by="mistralai",
                description="Mistral 7B model (mock)"
            ),
            ModelInfo(
                id="mistral-medium",
                created=1677649963,
                owned_by="mistralai",
                description="Medium Mistral model (mock)"
            ),
        ]
        
        return ModelsListResponse(data=mock_models)


# Global mock orchestration service instance
mock_orchestration = MockOrchestrationService()
