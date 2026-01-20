"""
Prompt Engine Service.
Handles prompt templating, rewriting, and token budget management.
"""

from typing import List, Dict, Any, Optional
import tiktoken
import logging
from .templates import template_registry

logger = logging.getLogger(__name__)

class PromptEngine:
    def __init__(self):
        # Cache encoders to avoid reloading
        self._encoders = {}

    def _get_encoder(self, model_name: str = "gpt-3.5-turbo"):
        if model_name not in self._encoders:
            try:
                self._encoders[model_name] = tiktoken.encoding_for_model(model_name)
            except KeyError:
                # Fallback to cl100k_base (used by gpt-4, gpt-3.5-turbo)
                logger.warning(f"Could not find encoder for model {model_name}, usage cl100k_base")
                self._encoders[model_name] = tiktoken.get_encoding("cl100k_base")
        return self._encoders[model_name]

    def process_prompt(self, template_id: Optional[str] = None, variables: Optional[Dict[str, Any]] = None) -> str:
        """
        Process a prompt, potentially applying a template.
        """
        if not template_id:
            return ""
            
        template = template_registry.get_template(template_id)
        if not template:
            logger.warning(f"Template '{template_id}' not found.")
            return ""
            
        return template.render(variables or {})

    def process_prompt_from_content(self, content: str, variables: Optional[Dict[str, Any]] = None) -> str:
        """
        Process a prompt using raw template content.
        """
        if not content:
            return ""
            
        from .templates import PromptTemplate
        # Create temporary template object to use its render logic (Jinja2)
        # ID is dummy
        template = PromptTemplate(template_id="temp", content=content)
        return template.render(variables or {})

    def check_token_budget(self, text: str, budget: int, model_name: str = "gpt-3.5-turbo") -> bool:
        """
        Check if the prompt exceeds the token budget.
        """
        if not text:
            return True
            
        encoder = self._get_encoder(model_name)
        count = len(encoder.encode(text))
        
        is_safe = count <= budget
        if not is_safe:
            logger.warning(f"Token budget exceeded: {count} > {budget}")
            
        return is_safe
        
    def count_tokens(self, text: str, model_name: str = "gpt-3.5-turbo") -> int:
        """Helper to get token count."""
        encoder = self._get_encoder(model_name)
        return len(encoder.encode(text))

    async def rewrite_prompt(self, prompt: str, goal: str = "clarity") -> str:
        """
        Rewrite a prompt using the PromptRewriter.
        """
        from .rewriter import prompt_rewriter
        return await prompt_rewriter.rewrite(prompt, goal)

    async def assemble_context(self, query: str, collection_name: str, org_id: str, n_results: int = 3) -> str:
        """
        Retrieve context from the Data Engine and format it for the prompt.
        """
        try:
            # Lazy import to avoid circular dependencies if any
            from data.engine import data_engine
            
            docs = data_engine.retrieve_context(collection_name, query, org_id, n_results)
            
            if not docs:
                return ""
                
            # Format docs into a single context string
            # We can use a standardized format, e.g., numbered list
            formatted_context = "\n".join([f"{i+1}. {doc}" for i, doc in enumerate(docs)])
            return formatted_context
            
        except Exception as e:
            logger.error(f"Error assembling context: {e}")
            return ""

prompt_engine = PromptEngine()
