"""
Standard logging utility for InferiaLLM microservices.
"""

import logging
import sys
import json
from datetime import datetime, timezone
from typing import Optional
from inferia.common.http_client import request_id_ctx


# ---------------------------------------------------------------------------
# Formatters & filters (module-level so they are created once)
# ---------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    """Format logs as JSON for structured logging in production."""

    def __init__(self, service_name: Optional[str] = None):
        super().__init__()
        self._service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        log_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "service": self._service_name,
            "request_id": request_id_ctx.get(),
            "name": record.name,
            "module": record.module,
            "funcName": record.funcName,
            "line": record.lineno,
        }

        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)

        if hasattr(record, "extra_fields"):
            log_record.update(record.extra_fields)

        return json.dumps(log_record)


class TracingFormatter(logging.Formatter):
    """Human-readable formatter that injects the current request ID."""

    def format(self, record: logging.LogRecord) -> str:
        record.request_id = request_id_ctx.get() or "no-request-id"
        return super().format(record)


class _ServiceNameFilter(logging.Filter):
    """Injects service_name into every log record (used by Logstash handler)."""

    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def filter(self, record: logging.LogRecord) -> bool:
        record.service_name = self.service_name
        return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def setup_logging(
    level: str = "INFO",
    service_name: Optional[str] = None,
    use_json: bool = False,
    log_file: Optional[str] = None,
    logstash_host: Optional[str] = None,
    logstash_port: int = 5959,
    logstash_tags: Optional[list] = None,
    logger_name: Optional[str] = None,
) -> logging.Logger:
    """
    Configure and return an isolated logger for a microservice.

    Each call produces an independent named logger that does NOT propagate to
    the root logger, so multiple services in the same process cannot interfere
    with each other's handlers.

    Pass ``logger_name`` as the Python package prefix for the service (e.g.
    ``"inferia.services.api_gateway"``).  All sub-module loggers created with
    ``logging.getLogger(__name__)`` inside that package will propagate into
    this logger automatically via Python's logger hierarchy.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        service_name: Human-readable service label used in log output
        use_json: Whether to use JSON formatting for production
        log_file: Optional path to a file to write logs to
        logstash_host: Hostname of the Logstash server (enables handler when set)
        logstash_port: TCP port Logstash is listening on (default: 5959)
        logstash_tags: Optional list of tags to attach to every Logstash record
        logger_name: Python package path to use as the logger namespace
                     (e.g. ``"inferia.services.api_gateway"``).  Falls back to
                     ``service_name`` if not provided.

    Returns:
        The configured service logger.
    """
    # Silence the root logger — each service manages its own namespace
    root = logging.getLogger()
    if not any(isinstance(h, logging.NullHandler) for h in root.handlers):
        root.addHandler(logging.NullHandler())

    namespace = logger_name or service_name or "inferia"
    service_logger = logging.getLogger(namespace)

    # Isolate: do not bubble up to root
    service_logger.propagate = False

    # Clear any handlers from a previous call (e.g., during tests)
    service_logger.handlers.clear()

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    service_logger.setLevel(numeric_level)

    # --- Formatter ---
    if use_json:
        formatter = JsonFormatter(service_name=service_name)
    else:
        format_str = (
            f"%(asctime)s - [%(request_id)s] - "
            f"{service_name + ' - ' if service_name else ''}"
            f"%(name)s - %(levelname)s - %(message)s"
        )
        formatter = TracingFormatter(format_str)

    # --- Stream handler (stdout) ---
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    service_logger.addHandler(stream_handler)

    # --- Optional file handler ---
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        service_logger.addHandler(file_handler)

    # --- Optional Logstash handler ---
    if logstash_host:
        try:
            import logstash
            logstash_handler = logstash.TCPLogstashHandler(
                logstash_host,
                logstash_port,
                version=1,
                tags=logstash_tags or [],
            )
            if service_name:
                logstash_handler.addFilter(_ServiceNameFilter(service_name))
            service_logger.addHandler(logstash_handler)
            service_logger.info(
                "Logstash handler enabled (%s:%s)", logstash_host, logstash_port
            )
        except ImportError:
            service_logger.warning(
                "python-logstash is not installed. "
                "Install it with: pip install 'inferiallm[logstash]'"
            )

    service_logger.info(
        "Logging initialized for %s (level=%s, json=%s)", namespace, level, use_json
    )
    return service_logger
