import uuid
import grpc
import asyncpg

from inferia.services.orchestration.v1 import scheduler_pb2_grpc


def uid():
    return uuid.uuid4().hex[:8]


async def scheduler_stub():
    channel = grpc.aio.insecure_channel("localhost:50051")
    return scheduler_pb2_grpc.SchedulerStub(channel)
