import psutil

def get_memory_info():
    mem = psutil.virtual_memory()
    return {
        "memory_total": mem.total,
        "memory_used": mem.used,
    }
