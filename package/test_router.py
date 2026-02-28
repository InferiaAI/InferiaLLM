import asyncio
from inferia.services.api_gateway.config import settings

def test():
    try:
        providers = settings.providers.model_dump()
        print(providers)
    except Exception as e:
        print("ERROR:", e)

if __name__ == "__main__":
    test()
