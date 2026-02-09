"""
Nosana Job Builder Module

Constructs container job definitions for Nosana DePIN deployments.
Supports vLLM, Ollama, and vLLM-Omni engines.
"""

from typing import Dict, Any, Optional, List
import json
import os

# Global API key for all Nosana deployments
NOSANA_INTERNAL_API_KEY = os.getenv("NOSANA_INTERNAL_API_KEY", "")


def create_vllm_job(
    model_id: str,
    image: str = "docker.io/vllm/vllm-openai:latest",
    hf_token: Optional[str] = None,
    api_key: Optional[str] = None,
    # Stability & Hardware
    gpu_util: float = 0.95,
    dtype: str = "auto",
    enforce_eager: bool = False,
    min_vram: int = 6,
    # Advanced Tuning
    max_model_len: int = 8192,
    max_num_seqs: int = 256,
    enable_chunked_prefill: bool = False,
    quantization: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build a Nosana job definition for vLLM inference server.
    
    Args:
        model_id: HuggingFace model ID (e.g., "meta-llama/Llama-3.1-8B-Instruct")
        image: Docker image for vLLM
        hf_token: HuggingFace token for gated models
        api_key: API key for authentication (uses global key if not provided)
        gpu_util: GPU memory utilization (0.0-1.0)
        dtype: Data type (auto, float16, bfloat16)
        enforce_eager: Disable CUDA graphs for stability
        min_vram: Minimum VRAM requirement in GB
        max_model_len: Maximum context length
        max_num_seqs: Maximum concurrent sequences
        enable_chunked_prefill: Enable chunked prefill for long contexts
        quantization: Quantization method (awq, gptq, etc.)
    
    Returns:
        Dict with 'op' (container operation) and 'meta' (job metadata)
    """
    # Use provided key or fall back to global
    effective_api_key = api_key or NOSANA_INTERNAL_API_KEY

    # Construct the Health Check Body
    health_body = json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content": "Respond with a single word: Ready"}],
        "stream": False
    })

    # Health Check Headers
    health_headers: Dict[str, str] = {"Content-Type": "application/json"}
    if effective_api_key:
        health_headers["Authorization"] = f"Bearer {effective_api_key}"

    # Prepare Environment Variables
    envs: Dict[str, str] = {}
    token_to_use = hf_token or os.getenv("HF_TOKEN")
    if token_to_use:
        envs["HF_TOKEN"] = token_to_use

    cmd_args = [
        "--model", model_id,
        "--served-model-name", model_id,
        "--port", "9000",
        "--max-model-len", str(max_model_len),
        "--gpu-memory-utilization", str(gpu_util),
        "--max-num-seqs", str(max_num_seqs),
        "--dtype", dtype,
        "--trust-remote-code"
    ]
    
    # Add quantization flag if provided
    if quantization:
        cmd_args.extend(["--quantization", quantization])

    # Inject API Key if present
    if effective_api_key:
        cmd_args.extend(["--api-key", effective_api_key])

    # Eager execution
    if enforce_eager:
        cmd_args.append("--enforce-eager")
        
    # Chunked Prefill
    if enable_chunked_prefill:
        cmd_args.append("--enable-chunked-prefill")

    container_op = {
        "id": model_id,
        "type": "container/run",
        "args": {
            "cmd": cmd_args,
            "env": envs,
            "gpu": True,
            "image": image,
            "expose": [
                {
                    "port": 9000,
                    "health_checks": [
                        {
                            "body": health_body,
                            "path": "/v1/chat/completions",
                            "type": "http",
                            "method": "POST",
                            "headers": health_headers,
                            "continuous": False,
                            "expected_status": 200
                        }
                    ]
                }
            ]
        },
    }
    
    meta_data = {
        "trigger": "dashboard",
        "system_requirements": {
            "required_cuda": ["11.8", "12.1", "12.2", "12.3", "12.4", "12.5", "12.6", "12.8", "12.9"],
            "required_vram": min_vram
        }
    }
    
    return {
        "op": container_op,
        "meta": meta_data
    }


def create_ollama_job(
    model_id: str,
    image: str = "docker.io/ollama/ollama:latest",
    api_key: Optional[str] = None,
    min_vram: int = 4,
) -> Dict[str, Any]:
    """
    Build a Nosana job definition for Ollama inference server.
    
    Uses Caddy as a reverse proxy for API key authentication when api_key is provided.
    
    Args:
        model_id: Ollama model name (e.g., "llama3", "mistral")
        image: Docker image for Ollama
        api_key: API key for authentication (uses global key if not provided)
        min_vram: Minimum VRAM requirement in GB
    
    Returns:
        Dict with 'op' (container operation) and 'meta' (job metadata)
    """
    # Use provided key or fall back to global
    effective_api_key = api_key or NOSANA_INTERNAL_API_KEY
    
    ollama_image = image if "ollama" in image else "docker.io/ollama/ollama:latest"
    
    final_cmd: List[str]
    exposed_port: int
    envs: Dict[str, str] = {}

    if effective_api_key:
        # Secure mode: Use Caddy as reverse proxy for authentication
        exposed_port = 8080 
        envs["MY_API_KEY"] = effective_api_key
        
        secure_script = (
            "apt-get update && apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl && "
            "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg && "
            "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list && "
            "apt-get update && apt-get install -y caddy && "
            "printf \":8080 {\\n  @auth {\\n    not header Authorization \\\"Bearer %s\\\"\\n  }\\n  respond @auth \\\"Unauthorized\\\" 401\\n  reverse_proxy localhost:11434 {\\n    flush_interval -1\\n  }\\n}\" \"$MY_API_KEY\" > Caddyfile && "
            "ollama serve & echo 'Waiting for Ollama...' && "
            "while ! curl -s http://localhost:11434 > /dev/null; do sleep 2; done && "
            f"echo 'Ollama is ready!' && ollama pull {model_id} && "
            "caddy run --config Caddyfile"
        )
        final_cmd = ["-c", secure_script]
    else:
        # Unsecured mode: Direct Ollama access
        exposed_port = 11434
        final_cmd = [
            "-c", 
            f"ollama serve & sleep 5 && ollama pull {model_id} && tail -f /dev/null"
        ]

    container_op = {
        "type": "container/run",
        "id": "ollama-service",
        "args": {
            "image": ollama_image,
            "entrypoint": ["/bin/sh"],
            "cmd": final_cmd,
            "env": envs,
            "gpu": True,
            "expose": exposed_port 
        }
    }
    
    meta_data = {
        "trigger": "dashboard",
        "system_requirements": {
            "required_cuda": ["11.8", "12.0", "12.1", "12.2", "12.3", "12.4", "12.5", "12.6", "12.8", "12.9"],
            "required_vram": min_vram
        }
    }

    return {
        "op": container_op,
        "meta": meta_data
    }


def create_vllm_omni_job(
    model_id: str,
    image: str = "docker.io/vllm/vllm-omni:v0.11.0rc1",
    hf_token: Optional[str] = None,
    api_key: Optional[str] = None,
    # Stability & Hardware
    gpu_util: float = 0.95,
    dtype: str = "auto",
    enforce_eager: bool = False,
    min_vram: int = 16,
    # Advanced Tuning
    max_model_len: int = 8192,
    max_num_seqs: int = 64,
    limit_mm_per_prompt: str = "image=1,video=1",
) -> Dict[str, Any]:
    """
    Build a Nosana job definition for vLLM-Omni multimodal inference server.
    
    Args:
        model_id: HuggingFace model ID for multimodal model
        image: Docker image for vLLM-Omni
        hf_token: HuggingFace token for gated models
        api_key: API key for authentication (uses global key if not provided)
        gpu_util: GPU memory utilization (0.0-1.0)
        dtype: Data type (auto, float16, bfloat16)
        enforce_eager: Disable CUDA graphs for stability
        min_vram: Minimum VRAM requirement in GB
        max_model_len: Maximum context length
        max_num_seqs: Maximum concurrent sequences (lower for multimodal)
        limit_mm_per_prompt: Multimodal input limits
    
    Returns:
        Dict with 'op' (container operation) and 'meta' (job metadata)
    """
    # Use provided key or fall back to global
    effective_api_key = api_key or NOSANA_INTERNAL_API_KEY
    
    health_body = json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content": "Ready?"}],
        "stream": False
    })

    health_headers: Dict[str, str] = {"Content-Type": "application/json"}
    if effective_api_key:
        health_headers["Authorization"] = f"Bearer {effective_api_key}"

    envs: Dict[str, str] = {}
    token_to_use = hf_token or os.getenv("HF_TOKEN")
    if token_to_use:
        envs["HF_TOKEN"] = token_to_use

    # vLLM-Omni command args
    cmd_args = [
        model_id,
        "--served-model-name", model_id,
        "--host", "0.0.0.0",
        "--port", "9000",
        "--omni",
        "--trust-remote-code",
    ]
    
    if effective_api_key:
        cmd_args.extend(["--api-key", effective_api_key])

    container_op = {
        "id": f"vllm-omni-{model_id.replace('/', '-')}",
        "type": "container/run",
        "args": {
            "cmd": cmd_args,
            "env": envs,
            "gpu": True,
            "image": image,
            "expose": 9000
        },
    }
    
    meta_data = {
        "trigger": "dashboard",
        "system_requirements": {
            "required_cuda": ["11.8", "12.1", "12.2", "12.3", "12.4", "12.5", "12.6", "12.8", "12.9"],
            "required_vram": min_vram
        }
    }
    
    return {
        "op": container_op,
        "meta": meta_data
    }


def create_triton_job(
    model_id: str,
    image: str = "nvcr.io/nvidia/tritonserver:23.10-py3",
    api_key: Optional[str] = None,
    min_vram: int = 8,
) -> Dict[str, Any]:
    """
    Build a Nosana job definition for NVIDIA Triton Inference Server.
    
    Args:
        model_id: Model repository path (e.g. s3://bucket/models or /mnt/models)
        image: Triton server image
        api_key: API key for authentication (uses global key if not provided)
        min_vram: Minimum VRAM requirement in GB
    
    Returns:
        Dict with 'op' (container operation) and 'meta' (job metadata)
    """
    effective_api_key = api_key or NOSANA_INTERNAL_API_KEY
    
    # Triton default ports
    http_port = 8000
    grpc_port = 8001
    metrics_port = 8002
    
    exposed_port: int
    final_cmd: List[str]
    envs: Dict[str, str] = {}
    
    # Base Triton command
    # We use --model-control-mode=explicit (or poll) usually, but default is fine.
    # Essential to point to model repository.
    triton_cmd = (
        f"tritonserver --model-repository={model_id} "
        "--disable-auto-complete "
        "--http-port=8000 --grpc-port=8001 --metrics-port=8002"
    )

    if effective_api_key:
        # Secure mode: Use Caddy as reverse proxy
        exposed_port = 8080
        envs["MY_API_KEY"] = effective_api_key
        
        # Caddy setup script (similar to Ollama but proxying port 8000)
        secure_script = (
            "apt-get update && apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl && "
            "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg && "
            "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list && "
            "apt-get update && apt-get install -y caddy && "
            "printf \":8080 {\\n  @auth {\\n    not header Authorization \\\"Bearer %s\\\"\\n  }\\n  respond @auth \\\"Unauthorized\\\" 401\\n  reverse_proxy localhost:8000 {\\n    flush_interval -1\\n  }\\n}\" \"$MY_API_KEY\" > Caddyfile && "
            f"{triton_cmd} & "
            "echo 'Waiting for Triton...' && "
            "while ! curl -s http://localhost:8000/v2/health/ready > /dev/null; do sleep 2; done && "
            "echo 'Triton is ready!' && "
            "caddy run --config Caddyfile"
        )
        final_cmd = ["-c", secure_script]
    else:
        # Unsecured mode
        exposed_port = http_port
        final_cmd = ["-c", triton_cmd]

    container_op = {
        "type": "container/run",
        "id": "triton-service",
        "args": {
            "image": image,
            "entrypoint": ["/bin/sh"],
            "cmd": final_cmd,
            "env": envs,
            "gpu": True,
            "expose": exposed_port
        }
    }
    
    meta_data = {
        "trigger": "dashboard",
        "system_requirements": {
            "required_cuda": ["11.8", "12.0", "12.1", "12.2", "12.3", "12.4", "12.5", "12.6", "12.8", "12.9"],
            "required_vram": min_vram
        }
    }

    return {
        "op": container_op,
        "meta": meta_data
    }


def build_job_definition(
    engine: str,
    model_id: str,
    image: Optional[str] = None,
    hf_token: Optional[str] = None,
    api_key: Optional[str] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    High-level factory function to build job definitions based on engine type.
    
    Args:
        engine: Engine type ("vllm", "ollama", "vllm-omni")
        model_id: Model identifier
        image: Optional custom docker image
        hf_token: HuggingFace token
        api_key: API key for authentication
        **kwargs: Additional engine-specific arguments
    
    Returns:
        Complete Nosana job definition ready for posting
    """
    if engine == "vllm":
        job = create_vllm_job(
            model_id=model_id,
            image=image or "docker.io/vllm/vllm-openai:latest",
            hf_token=hf_token,
            api_key=api_key,
            **kwargs
        )
    elif engine == "ollama":
        job = create_ollama_job(
            model_id=model_id,
            image=image or "docker.io/ollama/ollama:latest",
            api_key=api_key,
            **{k: v for k, v in kwargs.items() if k in ["min_vram"]}
        )
    elif engine == "vllm-omni":
        job = create_vllm_omni_job(
            model_id=model_id,
            image=image or "docker.io/vllm/vllm-omni:v0.11.0rc1",
            hf_token=hf_token,
            api_key=api_key,
            **kwargs
        )
    elif engine == "triton":
        job = create_triton_job(
            model_id=model_id,
            image=image or "nvcr.io/nvidia/tritonserver:23.10-py3",
            api_key=api_key,
            **{k: v for k, v in kwargs.items() if k in ["min_vram"]}
        )
    else:
        raise ValueError(f"Unsupported engine: {engine}")
    
    
    # Return full job definition
    return {
        "version": "0.1",
        "type": "container",
        "meta": job["meta"],
        "ops": [job["op"]]
    }


def create_training_job(
    image: str,
    training_script: str,  # This can be a URL or a command string
    git_repo: Optional[str] = None,
    dataset_url: Optional[str] = None,
    base_model: Optional[str] = None,
    hf_token: Optional[str] = None,
    api_key: Optional[str] = None,
    # Hardware
    min_vram: int = 24, # Training usually needs more
    gpu_count: int = 1,
) -> Dict[str, Any]:
    """
    Build a Nosana job definition for a Training Job.
    
    Args:
        image: Training container image (e.g. pytorch/pytorch...)
        training_script: The command to run or script URL
        git_repo: Optional git repository to clone
        dataset_url: Optional dataset URL to download
        base_model: Base model ID if fine-tuning
        hf_token: HF Token
        api_key: API key
        min_vram: Min VRAM
        gpu_count: Number of GPUs
        
    Returns:
        Dict with 'op' and 'meta'
    """
    effective_api_key = api_key or NOSANA_INTERNAL_API_KEY
    
    envs: Dict[str, str] = {}
    token_to_use = hf_token or os.getenv("HF_TOKEN")
    if token_to_use:
        envs["HF_TOKEN"] = token_to_use

    if effective_api_key:
        envs["NOSANA_API_KEY"] = effective_api_key
        
    if git_repo:
        envs["GIT_REPO"] = git_repo
    if dataset_url:
        envs["DATASET_URL"] = dataset_url
    if base_model:
        envs["BASE_MODEL"] = base_model

    # Construct command
    # We assume the image has an entrypoint that handles GIT_REPO etc, 
    # OR we construct a shell command here to do it.
    # For robustness, let's construct a shell command sequence.
    
    cmd_parts = []
    
    # Harden command:
    # 1. Install System Deps (Git is often missing in bare PyTorch container)
    cmd_parts.append("apt-get update && apt-get install -y git wget")
    
    # 2. Clone Repo
    if git_repo:
        cmd_parts.append(f"git clone {git_repo} /workspace/repo && cd /workspace/repo")
        # 3. Install Python Dependencies
        cmd_parts.append("if [ -f requirements.txt ]; then pip install -r requirements.txt; fi")
        # Install common ML libs *with pinned versions* to match the PyTorch 2.1.2 base image
        cmd_parts.append("pip install transformers==4.38.2 datasets==2.18.0 tiktoken wandb")
        
        # 4. Data Preparation Heuristic (e.g. for nanoGPT)
        # If openwebtext/prepare.py exists, run it to generate train.bin
        cmd_parts.append("if [ -f data/openwebtext/prepare.py ]; then python data/openwebtext/prepare.py; elif [ -f prepare.py ]; then python prepare.py; fi")
    else:
        cmd_parts.append("mkdir -p /workspace && cd /workspace")
        
    # 4. Download Dataset
    if dataset_url:
         cmd_parts.append(f"wget -O dataset.tar.gz {dataset_url} && tar -xvf dataset.tar.gz")

    # 5. Run Script
    # If training_script looks like a file path, python it. If it's a command, run it.
    if training_script.endswith(".py"):
        cmd_parts.append(f"python {training_script}")
    elif training_script.endswith(".sh"):
        cmd_parts.append(f"bash {training_script}")
    else:
        cmd_parts.append(training_script) # Raw command
        
    # Wrap the entire chain in a subshell catch to keep container alive on failure for debugging
    # "set -e" ensures we abort the chain on first error, jumping to || sleep
    final_main_cmd = " && ".join(cmd_parts)
    final_cmd_str = f"(set -e; {final_main_cmd}) || {{ echo 'Job failed! Keeping container alive for 1 hour...'; sleep 3600; }}"
    
    container_op = {
        "id": "training-job",
        "type": "container/run",
        "args": {
            "cmd": ["/bin/bash", "-c", final_cmd_str],
            "env": envs,
            "gpu": True,
            "image": image,
            "expose": 6006 # Simple integer expose for robustness
        }
    }
    
    # Return full job definition structure matching working examples
    return {
        "version": "0.1",
        "type": "container",
        "meta": {
            "trigger": "dashboard",
            "system_requirements": {
                "required_cuda": ["11.8", "12.1", "12.2", "12.3", "12.4", "12.5", "12.6", "12.8", "12.9"],
                "required_vram": min_vram,
                "required_gpu": gpu_count
            }
        },
        "ops": [container_op]
    }

