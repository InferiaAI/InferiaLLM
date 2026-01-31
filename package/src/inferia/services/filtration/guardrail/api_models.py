from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field
from enum import Enum

class ScanType(str, Enum):
    INPUT = "input"
    OUTPUT = "output"

class GuardrailScanRequest(BaseModel):
    text: str = Field(..., description="Text content to scan")
    scan_type: ScanType = Field(..., description="Type of scan: input (prompt) or output (response)")
    user_id: Optional[str] = Field(None, description="User context ID")
    context: Optional[str] = Field(None, description="Original input context (required for output scans)")
    custom_banned_keywords: Optional[List[str]] = Field(None, description="Transient list of keywords to ban for this request")
    pii_entities: Optional[List[str]] = Field(None, description="PII entity types to detect/redact (e.g. EMAIL_ADDRESS, PERSON)")
    config: Optional[Dict[str, Any]] = Field(None, description="Dynamic configuration for scanners (thresholds, enabled toggles)")

class GuardrailScanResponse(BaseModel):
    is_valid: bool
    sanitized_text: Optional[str] = None
    risk_score: float
    violations: List[Any] = []
    scan_time_ms: float
    actions_taken: List[str] = []
