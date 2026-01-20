def build_llmd_spec(
    *,
    deployment_id: str,
    model,
    replicas: int,
    gpu_per_replica: int,
    node_names: list[str],
):
    assert len(node_names) == replicas

    return {
        "apiVersion": "llmd.ai/v1",
        "kind": "LLMDeployment",
        "metadata": {
            "name": f"llmd-{deployment_id}",
            "labels": {
                "deployment_id": deployment_id
            },
        },
        "spec": {
            "replicas": replicas,
            "model": {
                "uri": model["artifact_uri"],
                "format": "hf",
            },
            "runtime": {
                "backend": model["backend"],
                **(model["config"] or {}),
            },
            "placement": {
                "nodeSelector": {
                    "kubernetes.io/hostname": node_names[0]
                }
            },
            "resources": {
                "limits": {
                    "nvidia.com/gpu": gpu_per_replica
                }
            },
            "service": {
                "type": "ClusterIP",
                "port": 8000
            }
        }
    }
