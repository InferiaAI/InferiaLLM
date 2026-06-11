# services/placement_engine/service.py

from uuid import UUID
from inferia.services.orchestration.v1 import (
    placement_engine_pb2,
    placement_engine_pb2_grpc,
)
from inferia.services.orchestration.services.placement_engine.scoring import score_node


class PlacementEngineService(
    placement_engine_pb2_grpc.PlacementEngineServicer
):
    """
    PlacementEngine = Read-only advisor.

    Responsibilities:
    - Filter READY + healthy nodes
    - Score candidates
    - Recommend best node

    Explicitly NOT responsible for:
    - Provisioning
    - Autoscaling
    - Resource reservation
    """

    def __init__(self, repo):
        self.repo = repo

    async def PlaceWorkload(self, request, context):
        pool_id = UUID(request.pool_id)

        # ------------------------------------------------
        # READ-ONLY INVENTORY QUERY
        # ------------------------------------------------
        candidates = await self.repo.fetch_candidate_nodes(
            pool_id=pool_id,
            gpu_req=request.gpu_required,
            vcpu_req=request.vcpu_required,
            ram_req=request.ram_gb_required,
        )

        # ------------------------------------------------
        # NO SIDE EFFECTS — JUST ADVICE
        # ------------------------------------------------
        if not candidates:
            return placement_engine_pb2.PlaceWorkloadResponse(
                accepted=False,
                rejection_reason="NO_CAPACITY_AVAILABLE"
            )

        # ------------------------------------------------
        # STATE-AWARE SCORING
        # ------------------------------------------------
        best_node = min(candidates, key=score_node)

        return placement_engine_pb2.PlaceWorkloadResponse(
            accepted=True,
            node_id=str(best_node["node_id"])
        )
