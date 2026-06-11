import asyncio
from inferia.services.orchestration.server import serve


def start_api():
    """Start the Orchestration Service (HTTP + gRPC)."""
    asyncio.run(serve())


if __name__ == "__main__":
    start_api()
