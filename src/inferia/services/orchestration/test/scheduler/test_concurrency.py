import asyncio
import uuid
import grpc

from inferia.services.orchestration.v1 import (
    scheduler_pb2,
    scheduler_pb2_grpc,
)

GRPC_ENDPOINT = "localhost:50051"

# PUT A REAL READY NODE ID HERE
NODE_ID = "398ff465-7dc5-498f-8d66-336bb081c671"


async def allocate(stub, allocation_id):
    """
    Single allocation attempt
    """
    return await stub.Allocate(
        scheduler_pb2.AllocateRequest(
            allocation_id=str(allocation_id),
            node_id=NODE_ID,
            gpu=0,
            vcpu=1,
            ram_gb=1,
        )
    )


async def test_concurrent_allocation():
    async with grpc.aio.insecure_channel(GRPC_ENDPOINT) as channel:
        stub = scheduler_pb2_grpc.SchedulerStub(channel)

        print("\n=== CONCURRENT ALLOCATION TEST ===")

        # Two concurrent allocation attempts
        allocation_ids = [uuid.uuid4(), uuid.uuid4()]

        results = await asyncio.gather(
            allocate(stub, allocation_ids[0]),
            allocate(stub, allocation_ids[1]),
        )

        successes = [r for r in results if r.success]
        failures = [r for r in results if not r.success]

        print("Results:")
        for r in results:
            print("  success =", r.success, "| reason =", r.reason)

        # Assertions (manual, explicit)
        if len(successes) != 1:
            raise AssertionError(
                f"Expected exactly 1 success, got {len(successes)}"
            )

        if failures[0].reason != "INSUFFICIENT_CAPACITY":
            raise AssertionError(
                f"Unexpected failure reason: {failures[0].reason}"
            )

        print("✔ Concurrency protection verified")

        # -----------------------------------------------------
        # RELEASE & RE-ALLOCATE
        # -----------------------------------------------------
        print("\n=== RELEASE & REALLOCATE TEST ===")

        # Release the successful allocation
        success_index = results.index(successes[0])
        await stub.Release(
            scheduler_pb2.ReleaseRequest(
                allocation_id=str(allocation_ids[success_index])
            )
        )

        print("✔ Allocation released")

        # Try allocating again (should succeed)
        retry_id = uuid.uuid4()
        retry = await allocate(stub, retry_id)

        if not retry.success:
            raise AssertionError(
                f"Re-allocation failed unexpectedly: {retry.reason}"
            )

        print("✔ Capacity restored after release")

        print("\n=== ✅ SCHEDULER TEST PASSED ===\n")


if __name__ == "__main__":
    asyncio.run(test_concurrent_allocation())
