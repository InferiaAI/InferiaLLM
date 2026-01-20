import sys
import os
from pathlib import Path

# Ensure we can import from this directory
gateway_dir = Path(__file__).parent
sys.path.insert(0, str(gateway_dir))

import uvicorn
from config import settings

def start_api():
    """Start the Inference Gateway API."""
    # Change to gateway directory so imports work correctly
    os.chdir(gateway_dir)
    uvicorn.run(
        "app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level=settings.log_level.lower(),
    )

if __name__ == "__main__":
    start_api()
