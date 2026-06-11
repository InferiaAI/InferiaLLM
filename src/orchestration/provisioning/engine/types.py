from dataclasses import dataclass
from typing import Optional, Dict


@dataclass
class NodeProvisionSpec:
    provider: str
    provider_instance_id: str
    instance_type: Optional[str]
    gpu_total: int
    vcpu_total: int
    ram_gb_total: int
    node_class: str
    metadata: Dict
