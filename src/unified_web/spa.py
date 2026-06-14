"""SPA static-file serving for the unified web app.

Serves a built single-page app: real files win; unknown NON-asset paths fall
back to ``index.html`` so client-side routing works on hard refresh / deep
links. Asset paths (a dot in the last path segment, e.g. ``app.js``) keep their
404 so a genuinely missing asset is reported as missing rather than masked by
the HTML shell.
"""

import os

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles
from starlette.responses import FileResponse, Response


class SPAStaticFiles(StaticFiles):
    """StaticFiles with an index.html fallback for client-side routing.

    A non-asset path (no dot in its last segment) that has no real file falls
    back to ``index.html`` so deep links / hard refreshes resolve client-side.
    Asset paths (e.g. ``app.js``) keep their 404 so a genuinely missing asset
    is reported as missing rather than masked by the HTML shell.

    Starlette's ``StaticFiles.get_response`` RAISES ``HTTPException(404)`` for a
    missing file (it does not return a 404 response), so the fallback must
    handle both the raised-exception path and a returned 404.
    """

    async def __call__(self, scope, receive, send) -> None:
        # The "/" SPA mount is the catch-all, so a WebSocket whose path matches
        # none of /api, /inf, /v2 falls through to here. StaticFiles only serves
        # HTTP and ``assert scope["type"] == "http"`` would raise AssertionError
        # (an ugly unhandled ASGI exception logged per stray WS). Reject the
        # websocket cleanly and ignore other non-http scopes instead.
        if scope["type"] == "websocket":
            try:
                await receive()  # consume the websocket.connect event
            except Exception:
                pass
            await send({"type": "websocket.close", "code": 1000})
            return
        if scope["type"] != "http":
            return
        await super().__call__(scope, receive, send)

    def _is_spa_route(self, path: str) -> bool:
        return "." not in path.rsplit("/", 1)[-1]

    def _index_response(self) -> FileResponse:
        # ``self.directory`` is a ``str`` in this Starlette version (not Path),
        # so join with os.path rather than the Path ``/`` operator.
        return FileResponse(os.path.join(str(self.directory), "index.html"))

    async def get_response(self, path: str, scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code == 404 and self._is_spa_route(path):
                return self._index_response()
            raise
        if response.status_code == 404 and self._is_spa_route(path):
            return self._index_response()
        return response
