"""
Inference Gateway - Client-Facing API
Proxies requests to the Filtration Gateway for security and policy enforcement,
then routes to the actual model provider.
"""

import asyncio
import json
import logging
import time
from typing import AsyncGenerator, Dict, Optional

import httpx
from client import filtration_client
from config import settings
from core.http_client import http_client
from core.orchestrator import OrchestrationService
from core.rate_limiter import rate_limiter
from core.service import GatewayService
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Inference Gateway - OpenAI Compatible Endpoint",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown_event():
    await http_client.close_client()
    await filtration_client.close_client()


def extract_api_key(authorization: str) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid API Key format")
    return authorization.split(" ")[1]


# stream_with_tracking removed - logic moved to core.orchestrator.OrchestrationService


@app.post("/v1/chat/completions")
async def create_completion(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str = Header(None),
):
    """
    Main chat completion endpoint.
    Delegates orchestration to OrchestrationService.
    """
    api_key = extract_api_key(authorization)
    body = await request.json()

    return await OrchestrationService.handle_completion(
        api_key=api_key, body=body, background_tasks=background_tasks
    )
