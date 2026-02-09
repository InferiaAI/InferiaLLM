from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import logging
import sys
from pathlib import Path
import os

# Add current directory to path so we can import engine using absolute imports
sys.path.append(str(Path(__file__).parent))

from inferia.services.guardrail.engine import guardrail_engine
from inferia.services.guardrail.models import GuardrailResult, ScanType, Violation

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("guardrail-service")

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start polling config from Gateway
    from inferia.services.guardrail.config_manager import config_manager
    from inferia.services.guardrail.service_config import guardrail_settings

    config_manager.start_polling(
        gateway_url=guardrail_settings.filtration_url,
        api_key=guardrail_settings.internal_api_key,
    )

    yield

    config_manager.stop_polling()


app = FastAPI(title="Guardrail Service", version="0.1.0", lifespan=lifespan)

# CORS configuration
# Allow requests from dashboard and other known origins
allowed_origins = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://localhost:5173,http://localhost:8001",
)
_allow_origins = [
    origin.strip() for origin in allowed_origins.split(",") if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)


class ScanRequest(BaseModel):
    text: str
    scan_type: ScanType = ScanType.INPUT
    user_id: Optional[str] = None
    context: Optional[str] = None  # For output scan
    config: Optional[Dict[str, Any]] = None
    custom_banned_keywords: Optional[List[str]] = None
    pii_entities: Optional[List[str]] = None


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "guardrail"}


@app.post("/scan", response_model=GuardrailResult)
async def scan(request: ScanRequest):
    """
    Scan text for safety violations.
    """
    try:
        if request.scan_type == ScanType.INPUT:
            result = await guardrail_engine.scan_input(
                prompt=request.text,
                user_id=str(request.user_id) if request.user_id else "unknown",
                custom_keywords=request.custom_banned_keywords or [],
                pii_entities=request.pii_entities or [],
                config=request.config or {},
            )
        else:
            result = await guardrail_engine.scan_output(
                prompt=request.context or "",
                output=request.text,
                user_id=str(request.user_id) if request.user_id else "unknown",
                custom_keywords=request.custom_banned_keywords or [],
                pii_entities=request.pii_entities or [],
                config=request.config or {},
            )
        return result
    except Exception as e:
        logger.error(f"Scan failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)
