def build_skypilot_spec(
    *,
    deployment_id: str,
    placement: dict,
    entrypoint: str,
) -> dict:
    """
    placement example:
    {
        "cloud": "aws",
        "gpu_type": "A100",
        "gpu_count": 1,
        "spot": True,
        "region": "us-east-1",
    }
    """

    return {
        "deployment_id": deployment_id,
        "cloud": placement.get("cloud", "aws"),
        "accelerator": placement["gpu_type"],
        "gpu_count": placement["gpu_count"],
        "use_spot": placement.get("spot", False),
        "region": placement.get("region"),
        "command": entrypoint,
    }
