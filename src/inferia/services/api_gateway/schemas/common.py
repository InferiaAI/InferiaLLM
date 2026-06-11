from inferia.common.schemas.common import (
    StandardHeaders,
    ErrorResponse,
    HealthCheckResponse,
)
from datetime import datetime, timezone


def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)
