class RuntimeResolver:
    def resolve(self, *, replicas, gpu_per_replica, engine=None, model_type=None, agent_kind=None):
        """
        Resolve deployment runtime strategy based on engine and model type.

        For image/video generation workloads (InferaDiffusion), routes to
        the localai strategy. For all other workloads, routes through vllm.
        """
        # Worker-kind nodes always go through the WS-based worker strategy,
        # regardless of engine — the worker container runs vllm/ollama/etc.
        # locally based on the spec we push.
        if agent_kind and str(agent_kind).lower() == "worker":
            return "worker"

        # Explicit engine-based routing
        if engine and engine.lower() in ("inferia-diffusion",):
            return "localai"

        if engine and engine.lower() in ("sglang",):
            return "vllm"

        # Model-type based routing
        if model_type and model_type.lower() in (
            "image",
            "image_generation",
            "image generation",
        ):
            return "localai"

        if model_type and model_type.lower() in (
            "video",
            "video_generation",
            "video generation",
        ):
            return "localai"

        # llmd strategy wiring is not complete in this service yet.
        # Route all requests through vllm strategy to avoid runtime key errors.
        # Strategy-level validation will reject unsupported replica/gpu shapes.
        return "vllm"
