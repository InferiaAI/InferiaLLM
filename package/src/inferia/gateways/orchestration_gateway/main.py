import asyncio
from inferia.gateways.orchestration_gateway.app import serve

def start_api():
    asyncio.run(serve())
