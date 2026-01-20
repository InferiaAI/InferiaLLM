# services/nosana_runtime/client.py

import aiohttp

class NosanaRuntimeClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def get_job(self, job_address: str):
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/jobs/{job_address}"
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
            
    async def stop_job(self, job_address: str):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/jobs/stop",
                json={"jobAddress": job_address},
            ) as resp:
                if resp.status != 200:
                    # Include the actual error message from the sidecar
                    # so the worker can detect "Account not found" and handle it gracefully
                    try:
                        error_data = await resp.json()
                        error_msg = error_data.get("error", str(error_data))
                    except:
                        error_msg = await resp.text()
                    raise RuntimeError(
                        f"Failed to stop job {job_address}: {error_msg}"
                    )
