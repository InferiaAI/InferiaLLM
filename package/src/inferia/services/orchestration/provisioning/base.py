from abc import ABC, abstractmethod

class Provisioner(ABC):

    @abstractmethod
    async def provision(self, request) -> str:
        """
        Provision compute.
        Returns an external cluster id.
        """
        pass

    @abstractmethod
    async def terminate(self, cluster_id: str):
        pass
