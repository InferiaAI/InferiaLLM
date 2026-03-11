"""Tests for text chunker — complex logic layer."""

import pytest

from inferia.services.data.chunker import TextChunker


class TestTextChunker:
    """Verify chunking logic for RAG."""

    def test_splits_on_paragraph_separator(self):
        """Text with paragraph breaks splits on \\n\\n first."""
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        chunker = TextChunker(chunk_size=50, chunk_overlap=0)
        chunks = chunker.split_text(text)
        assert len(chunks) >= 2
        assert "First paragraph." in chunks[0]

    def test_overlap_contains_repeated_content(self):
        """Chunks with overlap share content at boundaries."""
        # Create text large enough to require multiple chunks
        words = ["word" + str(i) for i in range(100)]
        text = " ".join(words)
        chunker = TextChunker(chunk_size=100, chunk_overlap=30)
        chunks = chunker.split_text(text)
        if len(chunks) >= 2:
            # The end of chunk 0 and start of chunk 1 should overlap
            # Check that some content appears in both adjacent chunks
            chunk0_words = set(chunks[0].split())
            chunk1_words = set(chunks[1].split())
            overlap = chunk0_words & chunk1_words
            assert len(overlap) > 0

    def test_single_chunk_larger_than_max(self):
        """Text that can't be split smaller than chunk_size still produces output."""
        text = "A" * 500  # No separators, single block
        chunker = TextChunker(chunk_size=100, chunk_overlap=0, separators=[""])
        chunks = chunker.split_text(text)
        assert len(chunks) >= 1
        # All content should be present
        total = "".join(chunks)
        assert len(total) >= 500

    def test_empty_text_returns_empty(self):
        chunker = TextChunker(chunk_size=100, chunk_overlap=0)
        chunks = chunker.split_text("")
        assert chunks == []
