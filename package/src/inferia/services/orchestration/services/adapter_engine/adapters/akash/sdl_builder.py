"""
Akash SDL Builder
Generates Stack Definition Language (YAML) for Akash deployments.
"""

from typing import Dict, Any, Optional
import yaml

def build_inference_sdl(
    image: str,
    service_name: str = "app",
    port: int = 8000,
    gpu_type: str = "nvidia-gpu",
    gpu_model: str = "*",
    gpu_units: int = 1,
    cpu_units: float = 2.0,
    memory_size: str = "4Gi",
    storage_size: str = "10Gi",
    env: Optional[Dict[str, str]] = None,
    command: Optional[List[str]] = None,
    args: Optional[List[str]] = None,
    volumes: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Builds an SDL for a flexible inference container (e.g. vLLM).
    
    Args:
        image: Docker image
        service_name: Name of the service (default "app" or "vllm")
        port: Port to expose
        gpu_type: usually "nvidia-gpu"
        gpu_model: Specific model like "rtxa6000" or "*"
        gpu_units: Number of GPUs
        cpu_units: vCPU count
        memory_size: RAM size (e.g. "32Gi")
        storage_size: Ephemeral storage size
        env: Environment variables
        command: Entrypoint override
        args: Command arguments
        volumes: List of volume definitions. Example:
                 [{"name": "shm", "mount": "/dev/shm", "size": "10Gi", "type": "ram"},
                  {"name": "data", "mount": "/root/.cache", "size": "100Gi", "type": "beta3"}]
    """
    envs = []
    if env:
        for k, v in env.items():
            # Handle empty values
             val = v if v is not None else ""
             envs.append(f"{k}={val}")

    # Process volumes to separate into 'storage' profiles and 'params' mounts
    storage_profiles = [{"size": storage_size}] # Default root ephemeral
    volume_mounts = []
    
    if volumes:
        for vol in volumes:
            # Profile resource definition
            profile_entry = {
                "name": vol["name"],
                "size": vol["size"],
                "attributes": {
                    "persistent": vol.get("persistent", False),
                    "class": vol.get("class", "beta2") # beta2=hdd, beta3=nvme usually
                }
            }
            if vol.get("type") == "ram":
                profile_entry["attributes"]["class"] = "ram"
                profile_entry["attributes"]["persistent"] = False
                
            storage_profiles.append(profile_entry)
            
            # Service mount definition
            mount_entry = {
                "mount": vol["mount"],
                "readOnly": vol.get("readOnly", False)
            }
            # The keys in SDL params are the volume names
            volume_mounts.append((vol["name"], mount_entry))

    # Construct Service
    service_def = {
        "image": image,
        "expose": [
            {
                "port": port,
                "as": port, # Expose as same port internally
                "to": [{"global": True}]
            }
        ],
        "env": envs,
        "resources": {
            "cpu": {"units": cpu_units},
            "memory": {"size": memory_size},
            "storage": storage_profiles,
            "gpu": {
                "units": gpu_units,
                "attributes": {
                    "vendor": {
                        "nvidia": [{"model": gpu_model}]
                    }
                }
            }
        }
    }

    if command:
        service_def["command"] = command
    if args:
        service_def["args"] = args
        
    # Add volume mounts to params if any
    if volume_mounts:
        if "params" not in service_def:
            service_def["params"] = {"storage": {}}
        for vol_name, mount_cfg in volume_mounts:
            service_def["params"]["storage"][vol_name] = mount_cfg

    sdl = {
        "version": "2.0",
        "services": {
            service_name: service_def
        },
        "profiles": {
            "compute": {
                service_name: {
                    "resources": {
                        "cpu": {"units": cpu_units},
                        "memory": {"size": memory_size},
                        "storage": storage_profiles,
                        "gpu": {
                            "units": gpu_units,
                            "attributes": {
                                "vendor": {
                                    "nvidia": [{"model": gpu_model}]
                                }
                            }
                        }
                    }
                }
            },
            "placement": {
                "dcloud": {
                    "pricing": {
                        service_name: {
                            "denom": "uakt",
                            "amount": 1000000 # 1 AKT default, bid will vary
                        }
                    },
                    "signedBy": {
                        "anyOf": ["*"]
                    }
                }
            }
        },
        "deployment": {
            service_name: {
                "dcloud": {
                    "profile": service_name,
                    "count": 1
                }
            }
        }
    }
    
    return yaml.dump(sdl, sort_keys=False)


def build_training_sdl(
    image: str,
    training_script: str, # URL or command
    git_repo: Optional[str] = None,
    dataset_url: Optional[str] = None,
    gpu_type: str = "nvidia-gpu",
    gpu_units: int = 1,
    cpu_units: float = 8.0,
    memory_size: str = "32Gi",
    storage_size: str = "100Gi",
    env: Optional[Dict[str, str]] = None
) -> str:
    """
    Builds an SDL for a training job.
    Uses a base image that can handle git cloning etc via entrypoint or passed commands.
    """
    envs = []
    if env:
        for k, v in env.items():
            envs.append(f"{k}={v}")
            
    if git_repo:
        envs.append(f"GIT_REPO={git_repo}")
    if dataset_url:
        envs.append(f"DATASET_URL={dataset_url}")
    if training_script:
        envs.append(f"TRAINING_SCRIPT={training_script}")

    # Training jobs generally don't need public exposure, but we might want SSH or Tensorboard
    # For now, let's expose one port just in case (e.g. 6006 for tensorboard)
    
    sdl = {
        "version": "2.0",
        "services": {
            "training-node": {
                "image": image,
                "command": [
                    "bash", "-c", 
                    "if [ -n \"$GIT_REPO\" ]; then git clone $GIT_REPO /workspace; cd /workspace; fi; "
                    "if [ -n \"$DATASET_URL\" ]; then wget $DATASET_URL -O dataset.tar.gz; tar -xvf dataset.tar.gz; fi; "
                    "if [ -n \"$TRAINING_SCRIPT\" ]; then $TRAINING_SCRIPT; else sleep infinity; fi"
                ],
                "expose": [
                     {
                        "port": 6006, # TensorBoard
                        "as": 80,
                        "to": [
                            {
                                "global": True
                            }
                        ]
                    }
                ],
                "env": envs,
                "resources": {
                    "cpu": {
                        "units": cpu_units
                    },
                    "memory": {
                        "size": memory_size
                    },
                    "storage": {
                        "size": storage_size
                    },
                    "gpu": {
                        "units": gpu_units,
                        "attributes": {
                            "vendor": {
                                "nvidia": [
                                    {
                                        "model": "*"
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        },
        "profiles": {
            "compute": {
                "training-node": {
                    "resources": {
                        "cpu": {
                            "units": cpu_units
                        },
                        "memory": {
                            "size": memory_size
                        },
                        "storage": {
                            "size": storage_size
                        },
                        "gpu": {
                            "units": gpu_units,
                            "attributes": {
                                "vendor": {
                                    "nvidia": [
                                        {
                                            "model": "*"
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            },
            "placement": {
                "dcloud": {
                    "pricing": {
                        "training-node": {
                            "denom": "uakt",
                            "amount": 1000 
                        }
                    },
                    "signedBy": {
                        "anyOf": ["*"]
                    }
                }
            }
        },
        "deployment": {
            "training-node": {
                "dcloud": {
                    "profile": "training-node",
                    "count": 1
                }
            }
        }
    }

    return yaml.dump(sdl, sort_keys=False)
