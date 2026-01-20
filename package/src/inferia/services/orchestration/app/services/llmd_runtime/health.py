import asyncio


async def wait_until_ready(
    *,
    client,
    resource_name: str,
    timeout_seconds: int = 180,
):
    deadline = asyncio.get_event_loop().time() + timeout_seconds

    while True:
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError("llm-d deployment not ready")

        obj = client.get(resource_name)

        status = obj.get("status", {})
        if status.get("phase") == "Ready":
            return

        if status.get("phase") == "Failed":
            raise RuntimeError(
                f"llm-d failed: {status.get('reason')}"
            )

        await asyncio.sleep(5)
