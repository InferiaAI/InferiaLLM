import uvicorn
from services.inference.config import settings


def start_api():
    """Start the Inference Service API."""
    uvicorn.run(
        "services.inference.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        workers=settings.workers,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    start_api()
