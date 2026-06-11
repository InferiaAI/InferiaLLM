"""Handler modules for each inference request type."""

from .completion import CompletionHandler
from .embedding import EmbeddingHandler
from .image import ImageHandler
from .video import VideoHandler

__all__ = [
    "CompletionHandler",
    "EmbeddingHandler",
    "ImageHandler",
    "VideoHandler",
]
