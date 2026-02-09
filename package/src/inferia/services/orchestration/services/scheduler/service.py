from uuid import UUID
from inferia.services.orchestration.v1 import scheduler_pb2, scheduler_pb2_grpc

class SchedulerService(scheduler_pb2_grpc.SchedulerServicer):
    def __init__(self, scheduler_repo, autoscaler_repo, job_repo):
        self.repo = scheduler_repo
        self.autoscaler_repo = autoscaler_repo
        self.job_repo = job_repo


    async def Allocate(self, request, context):
        ok, reason, pool_id = await self.repo.allocate_with_preemption(
            UUID(request.allocation_id),
            UUID(request.node_id),
            request.gpu,
            request.vcpu,
            request.ram_gb,
            request.priority,
            request.owner_type,
            request.owner_id,
        )

        if not ok and pool_id:
            await self.autoscaler_repo.incr_failures(pool_id)

        if ok and pool_id:
            await self.autoscaler_repo.reset_failures(pool_id)

        return scheduler_pb2.AllocateResponse(success=ok, reason=reason)

    async def Release(self, request, context):
        ok = await self.repo.release(UUID(request.allocation_id))
        return scheduler_pb2.ReleaseResponse(success=ok)

    async def AllocateGang(self, request, context):
        ok, reason = await self.repo.allocate_gang(
            job_id=UUID(request.job_id),
            node_ids=[UUID(n) for n in request.node_ids],
            gpu=request.gpu,
            vcpu=request.vcpu,
            ram_gb=request.ram_gb,
            priority=request.priority,
            owner_type=request.owner_type,
            owner_id=request.owner_id,
        )

        return scheduler_pb2.AllocateGangResponse(
            success=ok,
            reason=reason,
        )
    
    async def GetJob(self, request, context):
        row = await self.job_repo.get_job(UUID(request.job_id))
        if not row:
            return scheduler_pb2.GetJobResponse()

        return scheduler_pb2.GetJobResponse(
            job=scheduler_pb2.Job(
                job_id=str(row["job_id"]),
                owner_type=row["owner_type"],
                owner_id=row["owner_id"],
                gang_size=row["gang_size"],
                state=row["state"],
                created_at=row["created_at"].isoformat(),
            )
        )

    async def ListJobs(self, request, context):
        rows = await self.job_repo.list_jobs(
            request.owner_type,
            request.owner_id,
        )

        return scheduler_pb2.ListJobsResponse(
            jobs=[
                scheduler_pb2.Job(
                    job_id=str(r["job_id"]),
                    owner_type=r["owner_type"],
                    owner_id=r["owner_id"],
                    gang_size=r["gang_size"],
                    state=r["state"],
                    created_at=r["created_at"].isoformat(),
                )
                for r in rows
            ]
        )

    async def CancelJob(self, request, context):
        ok = await self.job_repo.cancel_job(UUID(request.job_id))
        return scheduler_pb2.CancelJobResponse(success=ok)


    async def ListJobAllocations(self, request, context):
        rows = await self.job_repo.list_allocations(UUID(request.job_id))
    
        return scheduler_pb2.ListJobAllocationsResponse(
            allocations=[
                scheduler_pb2.Allocation(
                    allocation_id=str(r["allocation_id"]),
                    node_id=str(r["node_id"]),
                    gpu=r["gpu"],
                    vcpu=r["vcpu"],
                    ram_gb=r["ram_gb"],
                    priority=r["priority"],
                )
                for r in rows
            ]
        )
    