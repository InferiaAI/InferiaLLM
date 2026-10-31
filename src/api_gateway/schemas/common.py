from datetime import datetime, timezone
from typing import Annotated

from pydantic import PlainSerializer, WithJsonSchema


def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_utc_iso(value: datetime) -> str:
    """Naive values are stored as UTC; say so, or browsers read them as local time."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


# A datetime that leaves the API with its UTC offset, e.g. "...T08:37:11+00:00".
UTCDateTime = Annotated[
    datetime,
    PlainSerializer(_as_utc_iso, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "format": "date-time"}),
]
