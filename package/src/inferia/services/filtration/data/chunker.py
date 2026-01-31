"""
Text Chunking Module.
Provides functionality to split large text into smaller semantic chunks for RAG.
"""

from typing import List
import re

class TextChunker:
    """
    Splits text into chunks of a specified size with overlap.
    Mimics LangChain's RecursiveCharacterTextSplitter.
    """
    def __init__(
        self, 
        chunk_size: int = 1000, 
        chunk_overlap: int = 200,
        separators: List[str] = None
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or ["\n\n", "\n", " ", ""]

    def split_text(self, text: str) -> List[str]:
        """
        Split text into chunks.
        """
        return self._split_text(text, self.separators)

    def _split_text(self, text: str, separators: List[str]) -> List[str]:
        """
        Recursive splitting logic.
        """
        final_chunks = []
        
        # Get appropriate separator
        separator = separators[-1]
        new_separators = []
        for i, _s in enumerate(separators):
            if _s == "":
                separator = _s
                break
            if re.search(re.escape(_s), text):
                separator = _s
                new_separators = separators[i + 1:]
                break
                
        # Split
        if separator:
            splits = text.split(separator)
        else:
            splits = list(text) # Character split if no separators left

        # Merge splits into chunks
        good_splits = []
        _separator = separator if separator else ""
        
        for s in splits:
             if self._length(s) < self.chunk_size:
                 good_splits.append(s)
             else:
                 if new_separators:
                     good_splits.extend(self._split_text(s, new_separators))
                 else:
                     good_splits.append(s)

        return self._merge_splits(good_splits, _separator)

    def _length(self, text: str) -> int:
        return len(text)

    def _merge_splits(self, splits: List[str], separator: str) -> List[str]:
        docs = []
        current_doc = []
        total_len = 0
        
        for d in splits:
            _len = self._length(d)
            
            if total_len + _len + (len(separator) if current_doc else 0) > self.chunk_size:
                if total_len > self.chunk_size:
                     # Warn: single split larger than chunk size
                     pass
                
                if current_doc:
                    doc = separator.join(current_doc)
                    if doc:
                        docs.append(doc)
                    
                    # Handle overlap
                    while total_len > self.chunk_overlap or (total_len + _len > self.chunk_size and total_len > 0):
                        total_len -= self._length(current_doc[0]) + (len(separator) if len(current_doc) > 1 else 0)
                        current_doc.pop(0)

            current_doc.append(d)
            total_len += _len + (len(separator) if len(current_doc) > 1 else 0)

        if current_doc:
            doc = separator.join(current_doc)
            if doc:
                docs.append(doc)
                
        return docs

# Global helper
chunker = TextChunker(chunk_size=1000, chunk_overlap=200)
