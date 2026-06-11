# Minimal but extensible instance spec map

INSTANCE_SPECS = {
    "g4dn.xlarge": {
        "cpu": 4,
        "memory_gb": 16,
        "gpu": 1,
        "gpu_type": "T4",
    },
    "g5.xlarge": {
        "cpu": 4,
        "memory_gb": 16,
        "gpu": 1,
        "gpu_type": "A10G",
    },
    "p3.2xlarge": {
        "cpu": 8,
        "memory_gb": 61,
        "gpu": 1,
        "gpu_type": "V100",
    },
    "p4d.24xlarge": {
        "cpu": 96,
        "memory_gb": 1152,
        "gpu": 8,
        "gpu_type": "A100",
    },
}
