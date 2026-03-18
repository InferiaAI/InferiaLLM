class RuntimeResolver:
    def resolve(self, *, replicas, gpu_per_replica, engine=None, model_type=None):
        """
        Resolve deployment runtime strategy based on engine and model type.

        For image generation workloads (LocalAI/Stable Diffusion), routes to
        the localai strategy. For all other workloads, routes through vllm.
        """
        # Explicit engine-based routing
        if engine and engine.lower() in (
            "localai",
            "localai-image",
            "stablediffusion",
            "inferiadiffusion",
            "inferia-diffusion",
        ):
            return "localai"

        # Model-type based routing
        if model_type and model_type.lower() in (
            "image",
            "image_generation",
            "image generation",
        ):
            return "localai"

        # llmd strategy wiring is not complete in this service yet.
        # Route all requests through vllm strategy to avoid runtime key errors.
        # Strategy-level validation will reject unsupported replica/gpu shapes.
        return "vllm"
