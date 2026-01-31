from services.llmd.spec_builder import build_llmd_spec


def build_spec(
    *,
    deployment_id: str,
    model: dict,
    replicas: int,
    gpu_per_replica: int,
    node_names: list[str],
):
    return build_llmd_spec(
        deployment_id=deployment_id,
        model=model,
        replicas=replicas,
        gpu_per_replica=gpu_per_replica,
        node_names=node_names,
    )
