import psutil

def get_cpu_info():
    return {
        "cpu_total": psutil.cpu_count(),
        "cpu_used": psutil.cpu_percent(interval=0.5),
    }
