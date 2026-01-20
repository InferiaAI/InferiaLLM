import uvicorn
from inferia.gateways.filtration_gateway.app import app

def start_api():
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
