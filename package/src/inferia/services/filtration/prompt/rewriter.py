"""
Prompt Rewriter Module.
Handles rewriting prompts for clarity, safety, or style using an LLM.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

class PromptRewriter:
    """
    Rewrites prompts using an internal LLM call.
    """
    def __init__(self, model_name: str = "gpt-3.5-turbo"):
        self.model_name = model_name
        
    async def rewrite(self, prompt: str, goal: str = "clarity") -> str:
        """
        Rewrite the prompt to achieve a specific goal using an LLM.
        """
        if not prompt:
            return ""
            
        logger.info(f"Rewriting prompt with goal: {goal}")
        
        from config import settings
        import httpx
        
        if settings.openai_api_key:
            try:
                system_prompt = self._get_system_prompt(goal)
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {settings.openai_api_key}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "model": "gpt-3.5-turbo",
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": prompt}
                            ],
                            "temperature": 0.3
                        }
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        if data.get("choices"):
                            return data["choices"][0]["message"]["content"]
                    else:
                        logger.warning(f"LLM Rewriter failed: {response.text}")
                        
            except Exception as e:
                logger.error(f"Error calling LLM for rewriting: {e}")
                
        else:
            logger.warning("No OpenAI API key configured. Using heuristic fallback.")
            return self._heuristic_rewrite(prompt, goal)
            
        return prompt

    def _get_system_prompt(self, goal: str) -> str:
        """Get system prompt for the specific goal."""
        prompts = {
            "clarity": "You are an expert prompt engineer. Rewrite the user's prompt to be more clear, concise, and unambiguous. Keep the original intent exactly the same.",
            "professionalism": "Rewrite the user's prompt to be more professional and polite. Remove slang and ensure formal tone.",
            "safety": "Rewrite the user's prompt to remove any unsafe content while preserving the core safe intent. If the intent is wholly unsafe, return a refusal explanation.",
            "detail": "Expand the user's prompt to include more necessary details for an LLM to answer effectively. Use best practices for prompt engineering."
        }
        return prompts.get(goal, prompts["clarity"])

    def _heuristic_rewrite(self, prompt: str, goal: str) -> str:
        """Simple rule-based fallback."""
        if goal == "mock_upper": # Keep for tests
            return prompt.upper()
            
        if goal == "professionalism":
            if not prompt.lower().startswith("please"):
                return f"Please kindly {prompt}"
        
        return prompt

prompt_rewriter = PromptRewriter()
