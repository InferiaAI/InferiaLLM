import codecs
import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional
from cachetools import LRUCache

logger = logging.getLogger(__name__)


class StreamProcessor:
    """
    Handles processing of SSE streams, including token counting patterns
    (OpenAI usage/content) and timing.
    """

    _tiktoken = None
    _tiktoken_checked = False
    _encoder_cache: LRUCache = LRUCache(maxsize=128)

    @staticmethod
    def _transform_line(
        line: str,
        model: Optional[str] = None,
        adapter: Any = None,
        state: Optional[Dict[str, Any]] = None,
        drop_usage_only: bool = False,
    ) -> Optional[str]:
        """Rewrite one SSE line. None means drop it entirely.

        Anything that is not a JSON `data:` event is returned untouched, which
        covers blank separators, comments and the `[DONE]` sentinel.

        An adapter may turn one event into none or several, so the result can
        hold more than one `data:` event separated by a blank line, or nothing
        at all when the provider sent something with no OpenAI equivalent.
        """
        carriage = "\r" if line.endswith("\r") else ""
        stripped = line.rstrip("\r")
        if not stripped.startswith("data: "):
            # While translating, the output is a fresh OpenAI stream, so the
            # provider's own SSE field lines (`event: message_start` and the
            # like) must not reach the client. Blank lines stay: they separate
            # events.
            translating = adapter is not None and not getattr(
                adapter, "stream_is_openai_format", True
            )
            if translating and stripped.strip():
                return None
            return line

        payload = stripped[6:].strip()
        if not payload or payload == "[DONE]":
            return line
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return line
        if not isinstance(event, dict):
            return line

        # `_parse_usage` has already taken the numbers from the raw chunk, so
        # this drops nothing. The client never asked for the frame.
        if drop_usage_only and event.get("usage") and not event.get("choices"):
            return None

        events = [event]
        if adapter is not None and not getattr(
            adapter, "stream_is_openai_format", True
        ):
            try:
                events = adapter.transform_stream_event(event, state) or []
            except Exception:
                # Drop it. Passing the provider's own event through would hand
                # the client a chunk it cannot parse, mid-stream and silently;
                # raising would throw away a response already half-read.
                logger.warning("stream transform failed", exc_info=True)
                events = []

        if model is not None:
            for ev in events:
                if isinstance(ev, dict) and "model" in ev:
                    ev["model"] = model

        if not events:
            return None
        return "\n\n".join(
            f"data: {json.dumps(ev, separators=(',', ':'))}{carriage}"
            for ev in events
        )

    @staticmethod
    def _sse(event: Any) -> bytes:
        """One event as an SSE frame. A string is emitted verbatim, for `[DONE]`."""
        body = (
            event
            if isinstance(event, str)
            else json.dumps(event, separators=(",", ":"))
        )
        return f"data: {body}\n\n".encode("utf-8")

    @staticmethod
    async def process_stream(
        stream_generator: AsyncGenerator,
        start_time: float,
        usage_tracker: Dict[str, Any],
        rewrite_model: Optional[str] = None,
        adapter: Any = None,
        drop_usage_chunk: bool = False,
    ) -> AsyncGenerator[bytes, None]:
        """
        Wraps a stream generator to track usage.

        Args:
            stream_generator: The raw byte stream from upstream
            start_time: Request start time (for TTFT)
            usage_tracker: Dict to update with 'prompt_tokens', 'completion_tokens', 'ttft_ms'
            rewrite_model: When set, every event's `model` is replaced with this
                name. Clients address a deployment by name and some OpenAI
                client libraries assert the response echoes back what they sent,
                but upstream reports its own id. Rewriting forces the stream to
                be re-framed into whole lines: upstream is read with
                `aiter_raw`, so one `data:` line can arrive split across chunks
                and a chunk cannot be rewritten on its own.
            adapter: When set, each event is passed through the provider
                adapter's `transform_stream_event` so the client receives
                OpenAI-format chunks whatever the provider sent. Without this
                a streaming request to a provider with its own SSE format
                returns that format verbatim, while the same request
                non-streaming is translated by `transform_response`.
            drop_usage_chunk: Set when the caller added
                `stream_options.include_usage` itself. The usage the flag
                produces is still recorded, but the chunk carrying it is not
                forwarded, because the client did not ask for it.
        """
        buffer = ""
        pending = ""
        # One per stream. Adapters are shared singletons, so a translator that
        # needs to remember anything between events keeps it here.
        stream_state: Dict[str, Any] = {}
        # Incremental, so a multibyte character split across two chunks is held
        # until its remaining bytes arrive. A plain bytes.decode(errors="ignore")
        # per chunk silently drops the leading part and eats the character.
        decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")

        def _one(line: str) -> Optional[str]:
            return StreamProcessor._transform_line(
                line, rewrite_model, adapter, stream_state, drop_usage_chunk
            )

        def _framed(text: str, flush: bool = False) -> str:
            """Whole rewritten lines, holding back any trailing fragment."""
            nonlocal pending
            pending += text
            if flush:
                out, pending = pending, ""
                if not out:
                    return ""
                return _one(out) or ""
            lines = pending.split("\n")
            pending = lines.pop()
            if not lines:
                return ""
            # A dropped line takes its newline with it, or the stream fills
            # with blank frames.
            return "".join(
                done + "\n" for done in (_one(ln) for ln in lines)
                if done is not None
            )

        try:
            async for chunk in stream_generator:
                # Parse usage and detect the first actual content token.
                has_content, buffer = StreamProcessor._parse_usage(
                    chunk, usage_tracker, buffer
                )
                if has_content and usage_tracker.get("ttft_ms") is None:
                    usage_tracker["ttft_ms"] = int((time.time() - start_time) * 1000)

                if rewrite_model is None and adapter is None:
                    yield chunk
                    continue

                text = (
                    decoder.decode(chunk)
                    if isinstance(chunk, bytes)
                    else str(chunk)
                )
                out = _framed(text)
                if out:
                    yield out.encode("utf-8")

            # Parse any trailing partial line.
            if buffer:
                has_content, _ = StreamProcessor._parse_usage(
                    b"", usage_tracker, buffer, flush=True
                )
                if has_content and usage_tracker.get("ttft_ms") is None:
                    usage_tracker["ttft_ms"] = int((time.time() - start_time) * 1000)

            if rewrite_model is not None or adapter is not None:
                out = _framed(decoder.decode(b"", final=True), flush=True)
                if out:
                    yield out.encode("utf-8")

            if adapter is not None and not getattr(
                adapter, "stream_is_openai_format", True
            ):
                for event in adapter.finalize_stream(stream_state):
                    yield StreamProcessor._sse(event)
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
    def estimate_prompt_tokens(
        cls, messages: List[Dict[str, Any]], model_name: str
    ) -> int:
        """
        Estimate prompt tokens from a list of chat messages.
        Falls back to tiktoken estimation if available.
        """
        if not messages:
            return 0

        total = 0
        encoder = cls._get_encoder(model_name)

        for msg in messages:
            content = msg.get("content", "")
            if content:
                if encoder is not None:
                    try:
                        total += len(encoder.encode(str(content)))
                    except Exception:
                        total += max(1, len(str(content).encode("utf-8")) // 4)
                else:
                    total += max(1, len(str(content).encode("utf-8")) // 4)

        # Add tokens for message format overhead (approximate)
        # Each message has role and format overhead (~4 tokens per message)
        total += len(messages) * 4

        return total

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
