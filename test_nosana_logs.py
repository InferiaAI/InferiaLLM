import asyncio
import websockets
import json
import argparse
import aiohttp


NOSANA_CLIENT_MANAGER_URL = "https://client-manager.k8s.prd.nosana.com"


async def get_nosana_signature() -> str:
    """
    Get the Nosana auth signature from the client-manager API.
    """
    url = f"{NOSANA_CLIENT_MANAGER_URL}/auth/sign-message/external"

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json={}) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get signature: {resp.status}")
            data = await resp.json()
            return data["signature"]


async def stream_nosana_logs(
    node_address: str, job_id: str, api_key: str = None, use_ssl: bool = True
):
    """
    Stream logs from Nosana node WebSocket.
    """
    # Get signature if no API key provided
    if not api_key:
        api_key = await get_nosana_signature()

    protocol = "wss" if use_ssl else "ws"
    ws_url = f"{protocol}://{node_address}.node.k8s.prd.nos.ci/flog"

    auth_header = f"nosana-auth:{api_key}"

    subscribe_msg = {
        "path": "/flog",
        "headers": {"Authorization": auth_header},
        "header": auth_header,
        "body": {"jobAddress": job_id, "address": node_address},
    }

    print(f"URL: {ws_url}")
    print(f"Auth: {auth_header}")
    print(f"Message: {json.dumps(subscribe_msg)}")
    print()

    try:
        async with websockets.connect(
            ws_url, additional_headers={"Authorization": auth_header}
        ) as ws:
            print("Connected! Sending subscription...")
            await ws.send(json.dumps(subscribe_msg))
            print("Waiting for logs...")

            count = 0
            async for msg in ws:
                count += 1
                print(
                    f"LOG {count}: {msg[:200]}..."
                    if len(msg) > 200
                    else f"LOG {count}: {msg}"
                )
                if count >= 10:
                    break
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Nosana log streaming")
    parser.add_argument("--node", "-n", required=True, help="Nosana node address")
    parser.add_argument("--job", "-j", required=True, help="Job ID")
    parser.add_argument(
        "--api-key",
        "-k",
        help="Nosana API key (optional, will fetch from client-manager if not provided)",
    )
    parser.add_argument("--ws", "-w", action="store_true", help="Use ws instead of wss")

    args = parser.parse_args()

    asyncio.run(
        stream_nosana_logs(
            node_address=args.node,
            job_id=args.job,
            api_key=args.api_key,
            use_ssl=not args.ws,
        )
    )
