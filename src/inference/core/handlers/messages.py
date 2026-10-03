"""Handler for `/v1/messages`, the Anthropic Messages API surface.

Translates in, runs the ordinary completion path, translates out. Nothing in
that path is aware of the surface, which is what keeps API key resolution,
quotas, rate limiting, routing and logging identical to
`/v1/chat/completions`.
"""

import codecs
import logging
from typing import Dict, Optional

from fastapi import BackgroundTasks
from fastapi.responses import StreamingResponse

from ..surfaces import anthropic as surface
from .completion import CompletionHandler

logger = logging.getLogger(__name__)


class MessagesHandler:
    """The Anthropic Messages surface over the OpenAI completion path."""

    @staticmethod
    async def handle(
        api_key: str,
        body: Dict,
        background_tasks: BackgroundTasks,
        ip_address: Optional[str] = None,
        sandbox: bool = False,
    ):
        result = await CompletionHandler.handle(
            api_key=api_key,
            body=surface.request_to_openai(body),
            background_tasks=background_tasks,
            ip_address=ip_address,
            sandbox=sandbox,
        )

        if isinstance(result, StreamingResponse):
            translator = surface.StreamTranslator(model=body.get("model") or "")
            result.body_iterator = MessagesHandler._translate_stream(
                result.body_iterator, translator
            )
            return result

        return surface.response_to_anthropic(result)

    @staticmethod
    async def _translate_stream(source, translator: "surface.StreamTranslator"):
        """OpenAI SSE in, Anthropic SSE out.

        `process_stream` yields whole lines, so an event never arrives split.
        The decoder is incremental anyway, because that guarantee belongs to a
        different module and would break quietly here if it ever changed.
        """
        decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
        try:
            async for chunk in source:
                text = (
                    decoder.decode(chunk)
                    if isinstance(chunk, bytes)
                    else str(chunk)
                )
                for event in surface.iter_openai_events(text):
                    if event is None:
                        continue  # `[DONE]`: Anthropic closes differently
                    for out in translator.chunk(event):
                        yield out
        finally:
            # The envelope must close even if the client disconnects or
            # upstream dies, or a reader is left waiting on `message_stop`.
            for out in translator.finish():
                yield out
