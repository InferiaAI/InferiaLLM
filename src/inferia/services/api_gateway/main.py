import uvicorn
from inferia.services.api_gateway.config import settings


def start_api():
    """Start the API Gateway Service."""
    uvicorn_kwargs = dict(
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        workers=settings.workers,
        log_level=settings.log_level.lower(),
        proxy_headers=settings.proxy_headers,
    )
    if settings.forwarded_allow_ips is not None:
        uvicorn_kwargs["forwarded_allow_ips"] = settings.forwarded_allow_ips
    uvicorn.run("inferia.services.api_gateway.app:app", **uvicorn_kwargs)


if __name__ == "__main__":
    start_api()
