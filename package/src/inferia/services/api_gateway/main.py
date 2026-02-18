import uvicorn
from inferia.services.api_gateway.config import settings


def start_api():
    """Start the API Gateway Service."""
    uvicorn.run(
        "inferia.services.api_gateway.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    start_api()
