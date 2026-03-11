"""
Tests for Jinja2 SSTI prevention in prompt templates.

Verifies that PromptTemplate.render() uses a SandboxedEnvironment
to block access to dangerous Python internals via template injection.
"""

import pytest
from jinja2.exceptions import SecurityError

from inferia.services.data.prompt_templates import PromptTemplate, template_registry


# -- SSTI payloads that must be blocked --

SSTI_PAYLOADS = [
    # Access __class__ to enumerate Python internals
    "{{ ''.__class__.__mro__[1].__subclasses__() }}",
    # Direct attribute access to builtins
    "{{ ''.__class__.__bases__[0].__subclasses__() }}",
    # Attempt to import os
    "{{ cycler.__init__.__globals__.os.popen('id').read() }}",
    # Access __globals__
    "{{ config.__class__.__init__.__globals__['os'].popen('id').read() }}",
    # Lipogram-style attribute traversal
    "{{ request.__class__.__mro__[2].__subclasses__() }}",
    # Attempt to read files via __builtins__
    "{{ self.__init__.__globals__.__builtins__.open('/etc/passwd').read() }}",
]


class TestPromptTemplateSSTIPrevention:
    """Verify that SSTI payloads are blocked by the sandboxed environment."""

    def test_ssti_payloads_blocked(self):
        """Each SSTI payload must either raise SecurityError or render harmlessly."""
        for payload in SSTI_PAYLOADS:
            template = PromptTemplate(template_id="test", content=payload)
            result = template.render({})
            # If it didn't raise, it must have returned the raw content
            # (the except branch in render) — meaning it failed to execute
            # the malicious code
            assert result == payload, (
                f"SSTI payload executed instead of being blocked.\n"
                f"Payload: {payload!r}\n"
                f"Result:  {result!r}"
            )

    def test_dunder_access_blocked(self):
        """Direct __class__ access must raise SecurityError from sandbox."""
        template = PromptTemplate(
            template_id="test",
            content="{{ ''.__class__ }}",
        )
        # render() catches exceptions and returns raw content
        result = template.render({})
        # Must not contain the actual class representation
        assert "<class 'str'>" not in result

    def test_getattr_on_dangerous_attrs_blocked(self):
        """Accessing __subclasses__, __globals__, etc. must be blocked."""
        dangerous_attrs = ["__subclasses__", "__globals__", "__builtins__", "__import__"]
        for attr in dangerous_attrs:
            template = PromptTemplate(
                template_id="test",
                content=f"{{{{ ''.__class__.{attr} }}}}",
            )
            result = template.render({})
            # Should return raw content (blocked by sandbox)
            assert attr in result, (
                f"Dangerous attribute {attr} was not blocked"
            )


class TestPromptTemplateNormalOperation:
    """Verify that normal template rendering still works correctly."""

    def test_simple_variable_substitution(self):
        template = PromptTemplate(
            template_id="test",
            content="Hello {{ name }}, welcome to {{ service }}!",
        )
        result = template.render({"name": "Alice", "service": "InferiaLLM"})
        assert result == "Hello Alice, welcome to InferiaLLM!"

    def test_missing_variable_renders_empty(self):
        template = PromptTemplate(
            template_id="test",
            content="Hello {{ name }}!",
        )
        result = template.render({})
        assert result == "Hello !"

    def test_conditional_template(self):
        template = PromptTemplate(
            template_id="test",
            content="{% if formal %}Dear {{ name }}{% else %}Hey {{ name }}{% endif %}",
        )
        assert template.render({"formal": True, "name": "Bob"}) == "Dear Bob"
        assert template.render({"formal": False, "name": "Bob"}) == "Hey Bob"

    def test_loop_template(self):
        template = PromptTemplate(
            template_id="test",
            content="{% for item in items %}{{ item }} {% endfor %}",
        )
        result = template.render({"items": ["a", "b", "c"]})
        assert result == "a b c "

    def test_filter_template(self):
        template = PromptTemplate(
            template_id="test",
            content="{{ name | upper }}",
        )
        result = template.render({"name": "alice"})
        assert result == "ALICE"

    def test_default_templates_render(self):
        """Verify built-in registry templates work with the sandbox."""
        cs_template = template_registry.get_template("customer_support")
        assert cs_template is not None
        result = cs_template.render({"company": "Acme", "query": "help me"})
        assert "Acme" in result
        assert "help me" in result

        sum_template = template_registry.get_template("summarizer")
        assert sum_template is not None
        result = sum_template.render({"word_count": "100", "text": "some text"})
        assert "100" in result
        assert "some text" in result

    def test_render_error_returns_raw_content(self):
        """Invalid template syntax should return raw content, not crash."""
        template = PromptTemplate(
            template_id="test",
            content="{{ invalid syntax {% }}",
        )
        result = template.render({})
        assert result == "{{ invalid syntax {% }}"
