"""
HTTP client for communicating with Filtration Gateway.
"""

import httpx
from typing import Dict, Any, Optional
from fastapi import HTTPException, status as http_status
import logging

from config import settings

logger = logging.getLogger(__name__)


class FiltrationGatewayClient:
    """Client for making requests to the Filtration Gateway."""
    
    def __init__(self):
        self.base_url = settings.filtration_gateway_url
        self.internal_key = settings.filtration_internal_key
        self.timeout = settings.request_timeout
    
    def _get_headers(self, auth_token: Optional[str] = None) -> Dict[str, str]:
        """Build headers for filtration gateway requests."""
        headers = {
            "X-Internal-API-Key": self.internal_key,
            "Content-Type": "application/json"
        }
        
        if auth_token:
            headers["Authorization"] = auth_token
        
        return headers
    
    async def login(self, username: str, password: str) -> Dict[str, Any]:
        """Proxy login request to filtration gateway."""
        url = f"{self.base_url}/auth/login"
        payload = {"username": username, "password": password}
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Filtration gateway login failed: {e}")
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Authentication failed")
            )
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to filtration gateway: {e}")
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Filtration gateway unavailable"
            )
    
    async def get_user_info(self, auth_token: str) -> Dict[str, Any]:
        """Get user information from filtration gateway."""
        url = f"{self.base_url}/auth/me"
        headers = self._get_headers(auth_token)
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Failed to get user info")
            )
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to filtration gateway: {e}")
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Filtration gateway unavailable"
            )
    
    async def create_completion(
        self,
        auth_token: str,
        payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Proxy completion request to filtration gateway."""
        url = f"{self.base_url}/internal/completions"
        headers = self._get_headers(auth_token)
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Completion request failed: {e.response.status_code}")
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Completion request failed")
            )
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to filtration gateway: {e}")
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Filtration gateway unavailable"
            )
    
    async def list_models(self, auth_token: str) -> Dict[str, Any]:
        """Get available models from filtration gateway."""
        url = f"{self.base_url}/internal/models"
        headers = self._get_headers(auth_token)
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Failed to list models")
            )
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to filtration gateway: {e}")
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Filtration gateway unavailable"
            )
    
    async def get_permissions(self, auth_token: str) -> Dict[str, Any]:
        """Get user permissions from filtration gateway."""
        url = f"{self.base_url}/auth/permissions"
        headers = self._get_headers(auth_token)
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Failed to get permissions")
            )
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to filtration gateway: {e}")
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Filtration gateway unavailable"
            )

    async def scan_content(
        self,
        auth_token: str,
        payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Proxy guardrail scan request to filtration gateway."""
        url = f"{self.base_url}/internal/guardrails/scan"
        headers = self._get_headers(auth_token)
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Guardrail scan failed: {e.response.status_code}")
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Guardrail scan failed")
            )
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to filtration gateway: {e}")
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Filtration gateway unavailable"
            )


# Global client instance
filtration_client = FiltrationGatewayClient()
