
import uvicorn
from config import settings

def start_api():
    """Start the Inference Gateway API."""
    uvicorn.run(
        "app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level=settings.log_level.lower(),
    )

if __name__ == "__main__":
    start_api()
