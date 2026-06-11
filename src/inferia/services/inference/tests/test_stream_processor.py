"""Tests for stream processor — complex logic layer."""

import pytest

from inferia.services.inference.core.stream_processor import StreamProcessor


class TestStreamProcessor:
    """Verify SSE stream processing logic."""

    def test_sse_usage_parsed_from_chunk(self):
        """Provider-reported usage extracted from SSE chunk."""
        tracker = {}
        chunk = b'data: {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}\n\n'
        has_content, remaining = StreamProcessor._parse_usage(chunk, tracker, "")
        assert tracker["prompt_tokens"] == 10
        assert tracker["completion_tokens"] == 5

    def test_content_in_delta_sets_has_content(self):
        """Content in delta triggers has_content=True."""
        tracker = {}
        chunk = b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n'
        has_content, remaining = StreamProcessor._parse_usage(chunk, tracker, "")
        assert has_content is True

    def test_partial_chunk_buffered(self):
        """Incomplete SSE line stays in buffer until next chunk."""
        tracker = {}
        chunk = b'data: {"choices": [{"delta": {"content'
        has_content, remaining = StreamProcessor._parse_usage(chunk, tracker, "")
        assert remaining != ""
        assert has_content is False

    def test_done_marker_ignored(self):
        """[DONE] marker does not crash or count as content."""
        tracker = {}
        chunk = b"data: [DONE]\n\n"
        has_content, remaining = StreamProcessor._parse_usage(chunk, tracker, "")
        assert has_content is False

    def test_estimate_prompt_tokens_empty_messages(self):
        """Empty message list returns 0."""
        result = StreamProcessor.estimate_prompt_tokens([], "gpt-3.5-turbo")
        assert result == 0

    def test_estimate_prompt_tokens_includes_overhead(self):
        """Token count includes per-message overhead."""
        messages = [{"role": "user", "content": "Hello"}]
        result = StreamProcessor.estimate_prompt_tokens(messages, "gpt-3.5-turbo")
        # Should include content tokens + 4 overhead per message
        assert result > 4
