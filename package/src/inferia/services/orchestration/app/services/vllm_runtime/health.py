import asyncio
import httpx


async def wait_until_ready(
    endpoint: str,
    timeout_seconds: int = 60,
):
    deadline = asyncio.get_event_loop().time() + timeout_seconds

    async with httpx.AsyncClient() as client:
        while True:
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError("vLLM did not become ready")

            try:
                resp = await client.get(
                    f"{endpoint}/v1/models",
                    timeout=2,
                )
                if resp.status_code == 200:
                    return
            except Exception:
                pass

            await asyncio.sleep(2)
