class RuntimeResolver:
    def resolve(self, *, replicas, gpu_per_replica):
        # llmd strategy wiring is not complete in this service yet.
        # Route all requests through vllm strategy to avoid runtime key errors.
        # Strategy-level validation will reject unsupported replica/gpu shapes.
        return "vllm"
