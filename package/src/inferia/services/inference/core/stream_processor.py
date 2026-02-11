import json
import logging
import time
from typing import Any, AsyncGenerator, Dict

logger = logging.getLogger(__name__)


class StreamProcessor:
    """
    Handles processing of SSE streams, including token counting patterns 
    (OpenAI usage/content) and timing.
    """

    _tiktoken = None
    _tiktoken_checked = False
    _encoder_cache: Dict[str, Any] = {}
    
    @staticmethod
    async def process_stream(
        stream_generator: AsyncGenerator,
        start_time: float,
        usage_tracker: Dict[str, Any],
    ) -> AsyncGenerator[bytes, None]:
        """
        Wraps a stream generator to track usage.
        
        Args:
            stream_generator: The raw byte stream from upstream
            start_time: Request start time (for TTFT)
            usage_tracker: Dict to update with 'prompt_tokens', 'completion_tokens', 'ttft_ms'
        """
        buffer = ""

        try:
            async for chunk in stream_generator:
                # Parse usage and detect the first actual content token.
                has_content, buffer = StreamProcessor._parse_usage(
                    chunk, usage_tracker, buffer
                )
                if has_content and usage_tracker.get("ttft_ms") is None:
                    usage_tracker["ttft_ms"] = int((time.time() - start_time) * 1000)

                yield chunk

            # Parse any trailing partial line.
            if buffer:
                has_content, _ = StreamProcessor._parse_usage(
                    b"", usage_tracker, buffer, flush=True
                )
                if has_content and usage_tracker.get("ttft_ms") is None:
                    usage_tracker["ttft_ms"] = int((time.time() - start_time) * 1000)
        except Exception as e:
            logger.error(f"Stream processing error: {e}")
            raise e

    @staticmethod
    def _parse_usage(
        chunk: bytes,
        usage_tracker: Dict[str, Any],
        buffer: str,
        flush: bool = False,
    ) -> tuple[bool, str]:
        """
        Attempts to parse OpenAI-style usage from chunks.
        Updates usage_tracker in-place and returns:
        (has_content_in_this_chunk, remaining_partial_buffer)
        """
        has_content = False
        try:
            chunk_str = (
                chunk.decode("utf-8", errors="ignore")
                if isinstance(chunk, bytes)
                else str(chunk)
            )
            data = f"{buffer}{chunk_str}"

            if flush:
                lines = data.split("\n")
                remaining = ""
            else:
                lines = data.split("\n")
                remaining = lines.pop() if lines else data

            for line in lines:
                line = line.rstrip("\r")
                if not line.startswith("data: "):
                    continue

                payload = line[6:].strip()
                if not payload or payload == "[DONE]":
                    continue

                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                # Case 1: Provider-reported usage in stream chunks (preferred).
                usage = event.get("usage")
                if isinstance(usage, dict):
                    usage_tracker["prompt_tokens"] = usage.get(
                        "prompt_tokens", usage_tracker.get("prompt_tokens", 0)
                    )
                    usage_tracker["completion_tokens"] = usage.get(
                        "completion_tokens", usage_tracker.get("completion_tokens", 0)
                    )
                    usage_tracker["_provider_usage_seen"] = True

                # Case 2: Fallback token estimate from streamed content when usage is absent.
                content = StreamProcessor._extract_content(event)
                if content:
                    has_content = True
                    if not usage_tracker.get("_provider_usage_seen"):
                        # Approximation only; replaced if provider usage arrives later.
                        usage_tracker["completion_tokens"] = usage_tracker.get(
                            "completion_tokens", 0
                        ) + StreamProcessor._estimate_tokens(content, usage_tracker)

            return has_content, remaining
        except Exception:
            return has_content, buffer

    @staticmethod
    def _extract_content(event: Dict[str, Any]) -> str:
        choices = event.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""

        first = choices[0] if isinstance(choices[0], dict) else {}
        delta = first.get("delta")
        if isinstance(delta, dict):
            content = delta.get("content")
            if isinstance(content, str):
                return content

        message = first.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content

        text = first.get("text")
        if isinstance(text, str):
            return text

        return ""

    @classmethod
    def _estimate_tokens(cls, text: str, usage_tracker: Dict[str, Any]) -> int:
        if not text:
            return 0

        model_name = usage_tracker.get("_tokenizer_model", "gpt-3.5-turbo")
        encoder = cls._get_encoder(model_name)
        if encoder is not None:
            try:
                return max(1, len(encoder.encode(text)))
            except Exception:
                pass

        # Byte-length fallback (avoids word-split; rough approximation).
        return max(1, (len(text.encode("utf-8")) + 3) // 4)

    @classmethod
    def _get_encoder(cls, model_name: str):
        if not cls._tiktoken_checked:
            try:
                import tiktoken  # type: ignore

                cls._tiktoken = tiktoken
            except Exception:
                cls._tiktoken = None
            finally:
                cls._tiktoken_checked = True

        if cls._tiktoken is None:
            return None

        cache_key = model_name or "cl100k_base"
        if cache_key in cls._encoder_cache:
            return cls._encoder_cache[cache_key]

        try:
            encoder = cls._tiktoken.encoding_for_model(model_name)
        except Exception:
            try:
                encoder = cls._tiktoken.get_encoding("cl100k_base")
            except Exception:
                return None

        cls._encoder_cache[cache_key] = encoder
        return encoder
