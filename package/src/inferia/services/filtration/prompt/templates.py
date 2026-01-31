"""
Prompt Templates Module.
Handles prompt construction via templates and variable substitution.
"""

from typing import Dict, Any, Optional
import logging
from jinja2 import Template

logger = logging.getLogger(__name__)

class PromptTemplate:
    """
    Represents a reusable prompt template with Jinja2-style variables.
    Example: "Hello {{ name }}, how are you?"
    """
    def __init__(self, template_id: str, content: str, description: str = ""):
        self.template_id = template_id
        self.content = content
        self.description = description
        
    def render(self, variables: Dict[str, Any]) -> str:
        """
        Substitute variables into the template content using Jinja2.
        """
        try:
            # Create a Jinja2 template from the content
            template = Template(self.content)
            # Render with the provided variables
            return template.render(**variables)
        except Exception as e:
            logger.error(f"Error rendering template '{self.template_id}': {e}")
            return self.content

class TemplateRegistry:
    """
    Registry to store and retrieve templates.
    Currently in-memory, but could be DB-backed.
    """
    def __init__(self):
        self._templates: Dict[str, PromptTemplate] = {}
        # Add some default templates
        self.add_template(PromptTemplate(
            template_id="customer_support",
            content="You are a helpful customer support agent for {{ company }}. User Query: {{ query }}",
            description="Standard customer support agent"
        ))
        self.add_template(PromptTemplate(
            template_id="summarizer",
            content="Summarize the following text in {{ word_count }} words: {{ text }}",
            description="Text summarization assistant"
        ))

    def add_template(self, template: PromptTemplate):
        self._templates[template.template_id] = template

    def get_template(self, template_id: str) -> Optional[PromptTemplate]:
        return self._templates.get(template_id)

template_registry = TemplateRegistry()
