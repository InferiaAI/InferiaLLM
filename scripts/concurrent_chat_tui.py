#!/usr/bin/env python3
"""
Concurrent terminal chat TUI with:
- 3 visible streaming panes
- configurable concurrent request batches
- background processing for extra requests
- realtime stats (TTFT, token speed, request states)
- shared chat context across turns

stdlib-only implementation (curses + http.client + threads).
"""

from __future__ import annotations

import argparse
import curses
import http.client
import json
import math
import os
import queue
import ssl
import textwrap
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urlparse


def now_ms() -> float:
    return time.perf_counter() * 1000.0


def approx_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def clip(s: str, max_len: int) -> str:
    if max_len <= 0:
        return ""
    if len(s) <= max_len:
        return s
    if max_len <= 3:
        return s[:max_len]
    return s[: max_len - 3] + "..."


@dataclass
class RequestState:
    request_id: int
    batch_id: int
    state: str = "queued"
    status_code: int = 0
    started_ms: float = 0.0
    ended_ms: float = 0.0
    ttft_ms: Optional[float] = None
    completion_tokens: int = 0
    token_speed_tps: float = 0.0
    text: str = ""
    error: str = ""


@dataclass
class BatchState:
    batch_id: int
    prompt: str
    request_ids: List[int] = field(default_factory=list)
    started_ms: float = field(default_factory=now_ms)


class ConcurrentChatApp:
    def __init__(
        self,
        url: str,
        api_key: str,
        model: str,
        default_concurrency: int,
        max_tokens: int,
        temperature: float,
        history_turns: int,
        system_prompt: str,
        max_parallel_connections: int = 100,
    ):
        self.url = url
        self.api_key = api_key
        self.model = model
        self.default_concurrency = max(1, default_concurrency)
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.history_turns = max(1, history_turns)
        self.system_prompt = system_prompt.strip()

        parsed = urlparse(self.url)
        self.scheme = parsed.scheme or "http"
        self.host = parsed.hostname or "localhost"
        self.port = parsed.port or (443 if self.scheme == "https" else 80)
        self.path = parsed.path or "/v1/chat/completions"

        self.ssl_context = (
            ssl.create_default_context() if self.scheme == "https" else None
        )

        self.events: queue.Queue[dict] = queue.Queue()
        self.requests: Dict[int, RequestState] = {}
        self.batches: Dict[int, BatchState] = {}

        self.next_request_id = 1
        self.next_batch_id = 1

        self.current_batch_id: Optional[int] = None
        self.visible_offset = 0
        self.visible_slots: List[Optional[int]] = [None, None, None]

        self.input_buffer = ""
        self.notice = "Type a prompt and press Enter. /help for commands."
        self.exit_requested = False

        # Chat context (without system prompt; appended on request build)
        self.context_messages: List[dict] = []
        self.last_turn_batch_id: Optional[int] = None
        self.last_turn_assistant_idx: Optional[int] = None

        self._lock = threading.Lock()

        # Connection pool semaphore to limit concurrent HTTP connections
        # This prevents "Too many open files" errors
        self._conn_semaphore = threading.Semaphore(max_parallel_connections)
        self._max_parallel_connections = max_parallel_connections

    def _build_messages(self, prompt: str) -> List[dict]:
        # Keep context bounded for predictable latency.
        tail = self.context_messages[-(self.history_turns * 2) :]
        messages: List[dict] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.extend(tail)
        messages.append({"role": "user", "content": prompt})
        return messages

    def _current_batch(self) -> Optional[BatchState]:
        if self.current_batch_id is None:
            return None
        return self.batches.get(self.current_batch_id)

    def _running_count(self) -> int:
        return sum(
            1 for r in self.requests.values() if r.state in ("queued", "running")
        )

    def _batch_running_count(self, batch_id: int) -> int:
        b = self.batches.get(batch_id)
        if not b:
            return 0
        return sum(
            1
            for rid in b.request_ids
            if rid in self.requests
            and self.requests[rid].state in ("queued", "running")
        )

    def _set_visible_for_current_batch(self):
        batch = self._current_batch()
        if not batch:
            self.visible_slots = [None, None, None]
            return
        ids = batch.request_ids
        slots = []
        for i in range(3):
            idx = self.visible_offset + i
            slots.append(ids[idx] if 0 <= idx < len(ids) else None)
        self.visible_slots = slots

    def _extract_piece(self, event: dict) -> str:
        choices = event.get("choices", [])
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0] if isinstance(choices[0], dict) else {}
        delta = first.get("delta")
        if isinstance(delta, dict):
            for key in ("content", "reasoning_content", "reasoning"):
                value = delta.get(key)
                if isinstance(value, str) and value:
                    return value
        message = first.get("message")
        if isinstance(message, dict):
            value = message.get("content")
            if isinstance(value, str):
                return value
        text = first.get("text")
        if isinstance(text, str):
            return text
        return ""

    def _worker_request(self, request_id: int, payload: dict):
        """Worker thread that makes HTTP request with connection pooling."""
        started = now_ms()
        self.events.put(
            {"type": "start", "request_id": request_id, "started_ms": started}
        )

        conn = None
        acquired = False
        try:
            # Use semaphore to limit concurrent HTTP connections
            # This prevents "Too many open files" errors with high concurrency
            acquired = self._conn_semaphore.acquire(timeout=60.0)
            if not acquired:
                raise Exception("Timeout waiting for available connection slot")

            if self.scheme == "https":
                conn = http.client.HTTPSConnection(
                    self.host,
                    self.port,
                    timeout=300,
                    context=self.ssl_context,
                )
            else:
                conn = http.client.HTTPConnection(self.host, self.port, timeout=300)

            raw = json.dumps(payload).encode("utf-8")
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Content-Length": str(len(raw)),
                "Connection": "close",
            }

            conn.request("POST", self.path, body=raw, headers=headers)
            response = conn.getresponse()

            self.events.put(
                {
                    "type": "response",
                    "request_id": request_id,
                    "status_code": int(response.status),
                }
            )

            if response.status != 200:
                body = response.read().decode("utf-8", errors="ignore")
                self.events.put(
                    {
                        "type": "error",
                        "request_id": request_id,
                        "status_code": int(response.status),
                        "error": clip(body, 500),
                        "ended_ms": now_ms(),
                    }
                )
                return

            first_token_seen = False
            first_token_ms = 0.0
            token_count = 0
            text_parts: List[str] = []

            while True:
                line = response.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="ignore").strip()
                if not decoded.startswith("data: "):
                    continue
                data = decoded[6:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue

                piece = self._extract_piece(event)
                if piece:
                    if not first_token_seen:
                        first_token_seen = True
                        first_token_ms = now_ms()
                    token_inc = approx_tokens(piece)
                    token_count += token_inc
                    text_parts.append(piece)
                    elapsed_s = max(
                        (now_ms() - (first_token_ms or started)) / 1000.0, 1e-6
                    )
                    tps = token_count / elapsed_s
                    self.events.put(
                        {
                            "type": "chunk",
                            "request_id": request_id,
                            "piece": piece,
                            "token_inc": token_inc,
                            "ttft_ms": (first_token_ms - started)
                            if first_token_seen
                            else None,
                            "token_speed_tps": tps,
                        }
                    )

            ended = now_ms()
            self.events.put(
                {
                    "type": "done",
                    "request_id": request_id,
                    "ended_ms": ended,
                    "text": "".join(text_parts),
                    "completion_tokens": token_count,
                }
            )
        except Exception as e:
            self.events.put(
                {
                    "type": "error",
                    "request_id": request_id,
                    "status_code": 0,
                    "error": clip(str(e), 500),
                    "ended_ms": now_ms(),
                }
            )
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            # Release semaphore after connection is fully closed
            if acquired:
                self._conn_semaphore.release()

    def submit_prompt(self, prompt: str, concurrency: Optional[int] = None):
        if self._running_count() > 0:
            self.notice = "A batch is still running. Wait, or use /q to quit."
            return

        prompt = prompt.strip()
        if not prompt:
            return

        n = max(1, concurrency or self.default_concurrency)
        batch_id = self.next_batch_id
        self.next_batch_id += 1

        payload_base = {
            "model": self.model,
            "messages": self._build_messages(prompt),
            "stream": True,
            "temperature": self.temperature,
        }
        if self.max_tokens > 0:
            payload_base["max_tokens"] = self.max_tokens

        # Add user turn to context after snapshotting payload messages.
        self.context_messages.append({"role": "user", "content": prompt})
        self.last_turn_assistant_idx = None
        self.last_turn_batch_id = batch_id

        batch = BatchState(batch_id=batch_id, prompt=prompt)
        self.batches[batch_id] = batch
        self.current_batch_id = batch_id
        self.visible_offset = 0

        for _ in range(n):
            request_id = self.next_request_id
            self.next_request_id += 1
            state = RequestState(
                request_id=request_id, batch_id=batch_id, state="queued"
            )
            self.requests[request_id] = state
            batch.request_ids.append(request_id)

            payload = dict(payload_base)
            t = threading.Thread(
                target=self._worker_request,
                args=(request_id, payload),
                daemon=True,
            )
            t.start()

        self._set_visible_for_current_batch()
        self.notice = f"Launched batch {batch_id} with {n} requests."

    def _maybe_set_context_assistant(self, req: RequestState):
        if self.last_turn_batch_id != req.batch_id:
            return
        if self.last_turn_assistant_idx is None:
            self.context_messages.append({"role": "assistant", "content": req.text})
            self.last_turn_assistant_idx = len(self.context_messages) - 1

    def select_context_response(self, request_id: int):
        req = self.requests.get(request_id)
        if not req:
            self.notice = f"Request {request_id} not found."
            return
        if req.state != "completed" or not req.text:
            self.notice = f"Request {request_id} is not completed with text yet."
            return
        if self.last_turn_batch_id != req.batch_id:
            self.notice = f"Request {request_id} is from an older batch."
            return
        if self.last_turn_assistant_idx is None:
            self.context_messages.append({"role": "assistant", "content": req.text})
            self.last_turn_assistant_idx = len(self.context_messages) - 1
        else:
            self.context_messages[self.last_turn_assistant_idx]["content"] = req.text
        self.notice = f"Context assistant set from request {request_id}."

    def process_events(self):
        while True:
            try:
                ev = self.events.get_nowait()
            except queue.Empty:
                break

            req = self.requests.get(ev.get("request_id"))
            if not req:
                continue

            ev_type = ev.get("type")
            if ev_type == "start":
                req.state = "running"
                req.started_ms = float(ev.get("started_ms") or now_ms())
            elif ev_type == "response":
                req.status_code = int(ev.get("status_code") or 0)
            elif ev_type == "chunk":
                piece = ev.get("piece") or ""
                req.text += piece
                req.completion_tokens += int(ev.get("token_inc") or 0)
                if req.ttft_ms is None and ev.get("ttft_ms") is not None:
                    req.ttft_ms = float(ev.get("ttft_ms"))
                req.token_speed_tps = float(ev.get("token_speed_tps") or 0.0)
            elif ev_type == "done":
                req.state = "completed"
                req.ended_ms = float(ev.get("ended_ms") or now_ms())
                req.status_code = req.status_code or 200
                if not req.text:
                    req.text = str(ev.get("text") or "")
                req.completion_tokens = max(
                    req.completion_tokens, int(ev.get("completion_tokens") or 0)
                )
                if req.ttft_ms is None:
                    req.ttft_ms = req.ended_ms - req.started_ms
                duration_from_ttft_s = max(
                    ((req.ended_ms - req.started_ms) - (req.ttft_ms or 0.0)) / 1000.0,
                    1e-6,
                )
                req.token_speed_tps = req.completion_tokens / duration_from_ttft_s
                if req.text:
                    self._maybe_set_context_assistant(req)
            elif ev_type == "error":
                req.state = "error"
                req.ended_ms = float(ev.get("ended_ms") or now_ms())
                req.status_code = int(ev.get("status_code") or 0)
                req.error = str(ev.get("error") or "unknown error")
                if req.ttft_ms is None and req.started_ms > 0:
                    req.ttft_ms = req.ended_ms - req.started_ms

        # Auto-close batch when all requests done
        batch = self._current_batch()
        if batch and self._batch_running_count(batch.batch_id) == 0:
            self.notice = f"Batch {batch.batch_id} completed. Use /use <id> to set context response."

    def _draw_box(self, stdscr, y: int, x: int, h: int, w: int):
        if h < 2 or w < 2:
            return
        self._safe_addstr(stdscr, y, x, "+" + "-" * (w - 2) + "+")
        for row in range(y + 1, y + h - 1):
            self._safe_addstr(stdscr, row, x, "|")
            self._safe_addstr(stdscr, row, x + w - 1, "|")
        self._safe_addstr(stdscr, y + h - 1, x, "+" + "-" * (w - 2) + "+")

    def _safe_addstr(self, stdscr, y: int, x: int, text: str, attr: int = 0):
        if text is None:
            return
        max_y, max_x = stdscr.getmaxyx()
        if y < 0 or y >= max_y:
            return
        # Avoid writing into the terminal's final column to prevent curses ERR on edge writes.
        if x < 0 or x >= max_x - 1:
            return
        max_chars = max_x - x - 1
        if max_chars <= 0:
            return
        out = text[:max_chars]
        if not out:
            return
        try:
            if attr:
                stdscr.addstr(y, x, out, attr)
            else:
                stdscr.addstr(y, x, out)
        except curses.error:
            return

    def _safe_move(self, stdscr, y: int, x: int):
        max_y, max_x = stdscr.getmaxyx()
        if max_y <= 0 or max_x <= 1:
            return
        y = max(0, min(max_y - 1, y))
        x = max(0, min(max_x - 2, x))
        try:
            stdscr.move(y, x)
        except curses.error:
            return

    def _pane_lines(
        self, req: Optional[RequestState], width: int, height: int
    ) -> List[str]:
        if width <= 1 or height <= 0:
            return []
        if req is None:
            return ["(empty)"][:height]

        if req.ttft_ms is not None:
            header = f"#{req.request_id} {req.state} sc={req.status_code} ttft={req.ttft_ms:.1f}ms "
        else:
            header = f"#{req.request_id} {req.state} sc={req.status_code} "
        tps = f"tps={req.token_speed_tps:.1f} tok={req.completion_tokens}"
        lines = [clip(header + tps, width)]

        body = (
            req.text
            if req.text
            else (req.error if req.error else "(waiting for chunks...)")
        )
        wrapped: List[str] = []
        for part in body.splitlines() or [""]:
            wrapped.extend(textwrap.wrap(part, width=width) or [""])

        if len(wrapped) > max(0, height - 1):
            wrapped = wrapped[-(height - 1) :]
        lines.extend(wrapped)
        return lines[:height]

    def _draw(self, stdscr):
        stdscr.erase()
        h, w = stdscr.getmaxyx()

        if h < 20 or w < 90:
            self._safe_addstr(
                stdscr, 0, 0, "Terminal too small. Please resize to at least 90x20."
            )
            stdscr.refresh()
            return

        title = "Concurrent Chat TUI (3 visible panes, background concurrency)"
        self._safe_addstr(stdscr, 0, 0, clip(title, w - 1), curses.A_BOLD)
        self._safe_addstr(
            stdscr,
            1,
            0,
            clip(
                f"URL={self.url}  model={self.model}  default_concurrency={self.default_concurrency}  running={self._running_count()}  max_conn={self._max_parallel_connections}",
                w - 1,
            ),
        )

        pane_top = 2
        input_h = 3
        status_h = 8
        pane_h = h - pane_top - status_h - input_h
        pane_w = (w - 2) // 3
        gap = 1

        # Draw 3 panes
        for i in range(3):
            x = i * (pane_w + gap)
            self._draw_box(stdscr, pane_top, x, pane_h, pane_w)
            rid = self.visible_slots[i]
            req = self.requests.get(rid) if rid is not None else None
            pane_title = f" Pane {i + 1} "
            if rid is not None:
                pane_title += f"(req {rid}) "
            self._safe_addstr(
                stdscr, pane_top, x + 2, clip(pane_title, pane_w - 4), curses.A_BOLD
            )
            lines = self._pane_lines(req, pane_w - 2, pane_h - 2)
            for idx, line in enumerate(lines):
                row = pane_top + 1 + idx
                if row >= pane_top + pane_h - 1:
                    break
                self._safe_addstr(stdscr, row, x + 1, clip(line, pane_w - 2))

        status_top = pane_top + pane_h
        self._draw_box(stdscr, status_top, 0, status_h, w)

        total = len(self.requests)
        queued = sum(1 for r in self.requests.values() if r.state == "queued")
        running = sum(1 for r in self.requests.values() if r.state == "running")
        completed = sum(1 for r in self.requests.values() if r.state == "completed")
        errors = sum(1 for r in self.requests.values() if r.state == "error")

        self._safe_addstr(
            stdscr,
            status_top + 1,
            2,
            clip(
                f"requests total={total} queued={queued} running={running} completed={completed} error={errors}",
                w - 4,
            ),
        )

        batch = self._current_batch()
        if batch:
            running_in_batch = self._batch_running_count(batch.batch_id)
            elapsed = max((now_ms() - batch.started_ms) / 1000.0, 1e-6)
            done_in_batch = len(
                [
                    rid
                    for rid in batch.request_ids
                    if self.requests[rid].state in ("completed", "error")
                ]
            )
            rps = done_in_batch / elapsed
            self._safe_addstr(
                stdscr,
                status_top + 2,
                2,
                clip(
                    f"active batch={batch.batch_id} size={len(batch.request_ids)} done={done_in_batch} running={running_in_batch} "
                    f"batch_rps={rps:.2f} visible_offset={self.visible_offset}",
                    w - 4,
                ),
            )
            self._safe_addstr(
                stdscr,
                status_top + 3,
                2,
                clip(f"prompt: {batch.prompt}", w - 4),
            )
        else:
            self._safe_addstr(stdscr, status_top + 2, 2, "no active batch")

        active_ids = sorted(
            [
                r.request_id
                for r in self.requests.values()
                if r.state in ("queued", "running")
            ]
        )[:12]
        self._safe_addstr(
            stdscr,
            status_top + 4,
            2,
            clip(f"in-flight request IDs: {active_ids if active_ids else '[]'}", w - 4),
        )

        self._safe_addstr(
            stdscr,
            status_top + 5,
            2,
            clip(
                "commands: /n <num>  /use <request_id>  /clear  /help  /q   | keys: [ and ] shift visible panes",
                w - 4,
            ),
        )
        self._safe_addstr(
            stdscr, status_top + 6, 2, clip(f"notice: {self.notice}", w - 4)
        )

        input_top = status_top + status_h
        self._draw_box(stdscr, input_top, 0, input_h, w)
        self._safe_addstr(stdscr, input_top + 1, 2, "prompt> ")
        max_input = max(0, w - 12)
        shown = clip(self.input_buffer, max_input)
        self._safe_addstr(stdscr, input_top + 1, 10, shown)
        self._safe_move(stdscr, input_top + 1, 10 + len(shown))

        stdscr.refresh()

    def _handle_command(self, raw: str):
        parts = raw.strip().split()
        if not parts:
            return
        cmd = parts[0].lower()

        if cmd in ("/q", "/quit", "/exit"):
            self.exit_requested = True
            return
        if cmd == "/help":
            self.notice = (
                "Enter text to launch a batch. Commands: /n <num>, /use <id>, /clear, /q. "
                "Use [ and ] to shift visible panes."
            )
            return
        if cmd == "/clear":
            if self._running_count() > 0:
                self.notice = "Cannot clear while batch is running."
                return
            self.requests.clear()
            self.batches.clear()
            self.current_batch_id = None
            self.visible_offset = 0
            self.visible_slots = [None, None, None]
            self.context_messages.clear()
            self.last_turn_assistant_idx = None
            self.last_turn_batch_id = None
            self.notice = "Cleared requests and context."
            return
        if cmd == "/n":
            if len(parts) != 2:
                self.notice = "Usage: /n <concurrency>"
                return
            try:
                n = int(parts[1])
            except ValueError:
                self.notice = "Invalid number."
                return
            if n < 1:
                self.notice = "Concurrency must be >= 1."
                return
            self.default_concurrency = n
            self.notice = f"Default concurrency set to {n}."
            return
        if cmd == "/use":
            if len(parts) != 2:
                self.notice = "Usage: /use <request_id>"
                return
            try:
                rid = int(parts[1])
            except ValueError:
                self.notice = "Invalid request_id."
                return
            self.select_context_response(rid)
            return

        self.notice = f"Unknown command: {cmd}"

    def _handle_enter(self):
        raw = self.input_buffer.strip()
        self.input_buffer = ""
        if not raw:
            return
        if raw.startswith("/"):
            self._handle_command(raw)
        else:
            # Optional per-prompt override: "@24 your prompt"
            concurrency_override: Optional[int] = None
            if raw.startswith("@") and " " in raw:
                head, rest = raw.split(" ", 1)
                if head[1:].isdigit():
                    concurrency_override = max(1, int(head[1:]))
                    raw = rest.strip()
            self.submit_prompt(raw, concurrency_override)

    def run(self, stdscr):
        curses.curs_set(1)
        stdscr.nodelay(True)
        stdscr.timeout(80)

        while not self.exit_requested:
            self.process_events()
            self._set_visible_for_current_batch()
            self._draw(stdscr)

            ch = stdscr.getch()
            if ch == -1:
                continue

            if ch in (curses.KEY_ENTER, 10, 13):
                self._handle_enter()
                continue
            if ch in (curses.KEY_BACKSPACE, 127, 8):
                self.input_buffer = self.input_buffer[:-1]
                continue
            if ch == 9:
                self.input_buffer += "    "
                continue
            if ch == 27:
                # ESC
                self.exit_requested = True
                continue

            # Pane shift keys when input line is empty
            if not self.input_buffer and ch == ord("["):
                self.visible_offset = max(0, self.visible_offset - 1)
                self._set_visible_for_current_batch()
                continue
            if not self.input_buffer and ch == ord("]"):
                batch = self._current_batch()
                max_offset = 0
                if batch:
                    max_offset = max(0, len(batch.request_ids) - 3)
                self.visible_offset = min(max_offset, self.visible_offset + 1)
                self._set_visible_for_current_batch()
                continue

            if 32 <= ch <= 126:
                self.input_buffer += chr(ch)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default=os.getenv("CHAT_API_URL", "http://localhost:8001/v1/chat/completions"),
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("CHAT_API_KEY", ""),
    )
    parser.add_argument(
        "--model",
        default=os.getenv("CHAT_MODEL", "openai/gpt-oss-20b"),
    )
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--max-tokens", type=int, default=2560)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--history-turns", type=int, default=8)
    parser.add_argument("--system-prompt", default=os.getenv("CHAT_SYSTEM_PROMPT", ""))
    parser.add_argument(
        "--max-parallel-connections",
        type=int,
        default=100,
        help="Max concurrent HTTP connections (prevents 'Too many open files')",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.api_key:
        raise SystemExit("Missing API key. Pass --api-key or set CHAT_API_KEY in env.")

    app = ConcurrentChatApp(
        url=args.url,
        api_key=args.api_key,
        model=args.model,
        default_concurrency=args.concurrency,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        history_turns=args.history_turns,
        system_prompt=args.system_prompt,
        max_parallel_connections=args.max_parallel_connections,
    )
    curses.wrapper(app.run)


if __name__ == "__main__":
    main()
