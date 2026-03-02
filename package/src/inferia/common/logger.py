"""
Standard logging utility for InferiaLLM microservices.
"""

import logging
import sys
import json
from datetime import datetime
from typing import Optional, Dict, Any
from inferia.common.http_client import request_id_ctx

class JsonFormatter(logging.Formatter):
    """
    Format logs as JSON for structured logging in production.
    """
    def format(self, record: logging.LogRecord) -> str:
        log_record = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "request_id": request_id_ctx.get(),
            "name": record.name,
            "module": record.module,
            "funcName": record.funcName,
            "line": record.lineno,
        }
        
        # Add exception info if present
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
            
        # Add extra fields if provided
        if hasattr(record, "extra_fields"):
            log_record.update(record.extra_fields)
            
        return json.dumps(log_record)

def setup_logging(
    level: str = "INFO",
    service_name: Optional[str] = None,
    use_json: bool = False,
    log_file: Optional[str] = None
):
    """
    Configure logging for a microservice.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        service_name: Name of the service to include in logs
        use_json: Whether to use JSON formatting for production
        log_file: Optional path to a file to write logs to
    """
    root_logger = logging.getLogger()
    
    # Clear existing handlers
    if root_logger.handlers:
        for handler in root_logger.handlers:
            root_logger.removeHandler(handler)
            
    # Set numeric level
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root_logger.setLevel(numeric_level)

    # Standard Stream Handler
    stream_handler = logging.StreamHandler(sys.stdout)
    
    if use_json:
        formatter = JsonFormatter()
    else:
        format_str = (
            f"%(asctime)s - [%(request_id)s] - {service_name + ' - ' if service_name else ''}"
            "%(name)s - %(levelname)s - %(message)s"
        )
        
        class TracingFormatter(logging.Formatter):
            def format(self, record):
                record.request_id = request_id_ctx.get() or "no-request-id"
                return super().format(record)
                
        formatter = TracingFormatter(format_str)
        
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    # Optional File Handler
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    logging.info(f"Logging initialized for {service_name or 'unknown service'} (level={level}, json={use_json})")
