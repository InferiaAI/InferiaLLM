def build_skypilot_spec(
    *,
    deployment_id: str,
    placement: dict,
    entrypoint: str,
) -> dict:
    """
    Build a SkyPilot-compatible spec from a placement dict.

    placement example:
    {
        "cloud": "gcp",
        "gpu_type": "L4",
        "gpu_count": 1,
        "spot": True,
        "region": "us-central1",
    }
    """
    cloud = placement.get("cloud", "aws")
    region = placement.get("region")

    return {
        "deployment_id": deployment_id,
        "infra": f"{cloud}/{region}" if region else cloud,
        "accelerator": f"{placement['gpu_type']}:{placement['gpu_count']}",
        "use_spot": placement.get("spot", False),
        "command": entrypoint,
    }
