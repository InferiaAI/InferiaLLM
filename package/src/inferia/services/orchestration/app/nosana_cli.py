import asyncio
import os
import argparse
import sys
from services.adapter_engine.adapters.nosana.nosana_adapter import NosanaAdapter


def print_menu(options):
    for i, opt in enumerate(options):
        print(f"[{i}] {opt}")

async def interactive_mode(adapter):
    print("\n--- Interactive Mode ---")
    
    # 1. Discover
    print("Fetching available markets (GPUs)...")
    resources = await adapter.discover_resources()
    if not resources:
        print("No resources found.")
        return

    # Display Resources
    print("\nAvailable GPU Markets:")
    for i, r in enumerate(resources):
        print(f"[{i}] {r['provider_resource_id']} | Type: {r.get('gpu_type')} | Price: ${r.get('price_per_hour')}/hr | Region: {r.get('region')}")

    # 2. Select Market
    while True:
        try:
            choice = input(f"\nSelect Market Index (0-{len(resources)-1}): ")
            idx = int(choice)
            if 0 <= idx < len(resources):
                selected_market = resources[idx]
                break
            else:
                print("Invalid index.")
        except ValueError:
            print("Invalid input.")

    market_id = selected_market['provider_resource_id']
    pool_id = selected_market.get('metadata', {}).get('address', 'unknown_address')
    
    print(f"\nSelected Market: {market_id} ({pool_id})")

    # 3. Select Image
    images = [
        "ubuntu:latest",
        "nvidia/cuda:11.8.0-base-ubuntu22.04",
        "nosana/start:latest",
        "Custom..."
    ]
    print("\nSelect Docker Image:")
    print_menu(images)
    
    image_choice = input("Select Image Index: ")
    try:
        img_idx = int(image_choice)
        if img_idx == len(images) - 1:
            image = input("Enter custom image (e.g. myrepo/myimage:tag): ")
        else:
            image = images[img_idx]
    except:
        image = "ubuntu:latest"
        print(f"Invalid selection, defaulting to {image}")

    # 4. Provision
    print(f"\nProvisioning {image} on {market_id}...")
    try:
        # Default cmd for now
        cmd = ["echo", "hello", "world"]
        
        node = await adapter.provision_node(
            provider_resource_id=image,
            pool_id=pool_id if pool_id != 'unknown_address' else '11111111111111111111111111111111', # Fallback for sim
            cmd=cmd
        )
        
        print("\nSUCCESS: Job Created and Running!")
        print(f"Job Address: {node['provider_instance_id']}")
        print(f"Metadata: {node.get('metadata')}")
        
        print("\nTo stopping this job (Deprovision), run:")
        print(f"python3 nosana_cli.py --deprovision {node['provider_instance_id']} --mode {os.getenv('NOSANA_MODE', 'simulation')}")

    except Exception as e:
        print(f"\nFAILURE: {e}")


async def main():
    parser = argparse.ArgumentParser(description="Nosana Adapter CLI")
    parser.add_argument("--mode", choices=["real", "simulation"], help="Operation mode (default: prompt in interactive)")
    parser.add_argument("--interactive", "-i", action="store_true", help="Run in interactive mode")
    parser.add_argument("--discover", action="store_true", help="Discover resources")
    parser.add_argument("--provision", action="store_true", help="Provision a node")
    parser.add_argument("--image", type=str, default="ubuntu:latest", help="Docker image to run")
    parser.add_argument("--cmd", type=str, nargs="+", default=["echo", "hello world"], help="Command to run in container")
    parser.add_argument("--market", type=str, default="11111111111111111111111111111111", help="Market/Pool ID")
    parser.add_argument("--deprovision", type=str, help="Deprovision Job Address")

    args = parser.parse_args()

    # Determine Mode
    mode = args.mode
    if not mode:
        # If no mode flag, check env or default to asking in interactive
        if args.interactive or (not args.discover and not args.provision and not args.deprovision):
             pass # Will ask in proper interactive block
        else:
             mode = os.getenv("NOSANA_MODE", "simulation")

    if not args.interactive and (args.discover or args.provision or args.deprovision):
        os.environ["NOSANA_MODE"] = mode if mode else "simulation"
        print(f"--- Nosana CLI (Mode: {os.environ['NOSANA_MODE']}) ---")
        adapter = NosanaAdapter()
        
        if args.discover:
            print("Discovering resources...")
            resources = await adapter.discover_resources()
            for i, r in enumerate(resources):
                print(f"[{i}] {r['provider_resource_id']} | Type: {r.get('gpu_type')} | Price: ${r.get('price_per_hour')}/hr")
        
        elif args.provision:
             # Existing provision logic...
             pass # (Simplified for brevity as user wants interactive, but keeping args logic is good)
             # Reuse existing logic or call helper? Let's just keep the file simple.
             # Actually I am replacing the whole file so I should fully implement this block or minimal version.
             # I will reimplement standard args logic briefly.
             try:
                node = await adapter.provision_node(args.image, args.market, args.cmd)
                print(f"Job: {node['provider_instance_id']}")
             except Exception as e:
                print(e)
        
        elif args.deprovision:
             try:
                await adapter.deprovision_node(args.deprovision)
                print("Stopped.")
             except Exception as e:
                print(e)
        return

    # Interactive Flow
    if not mode:
        m_choice = input("Select Mode (1=simulation, 2=real): ")
        mode = "real" if m_choice == "2" else "simulation"
    
    os.environ["NOSANA_MODE"] = mode
    adapter = NosanaAdapter()
    await interactive_mode(adapter)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
