import subprocess
import json


def get_gpu_info():
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits"]
        ).decode()

        gpus = []
        for line in out.strip().split("\n"):
            name, total, used = line.split(", ")
            gpus.append({
                "name": name,
                "memory_total": int(total),
                "memory_used": int(used),
            })
        return gpus
    except Exception:
        return []
