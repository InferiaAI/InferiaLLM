class RuntimeResolver:
    def resolve(self, *, replicas, gpu_per_replica):
        # decentralized clouds (Nosana, Akash, etc.)
        # if pool["provider"] in {"nosana", "akash"}:
        #     return "vllm"

        # centralized multi-node
        if replicas > 1 or gpu_per_replica > 1:
            return "llmd"
        elif gpu_per_replica == 1 and replicas == 1:
            return "vllm"

        return "vllm"
