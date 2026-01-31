import aiohttp
import asyncio
import json

async def check():
    url = "https://dashboard.k8s.prd.nos.ci/api/markets"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            print(f"Status: {resp.status}")
            data = await resp.json()
            if data:
                print("First market sample:")
                print(json.dumps(data[0], indent=2))
                
                print("\nChecking for 'address' field in all markets:")
                for m in data[:5]:
                    print(f"Slug: {m.get('slug')}, Address: {m.get('address')}")

if __name__ == "__main__":
    asyncio.run(check())
