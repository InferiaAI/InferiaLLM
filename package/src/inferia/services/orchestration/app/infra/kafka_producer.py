import json
from aiokafka import AIOKafkaProducer


class KafkaEventProducer:
    def __init__(self, bootstrap_servers: str):
        self.producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap_servers,
            acks="all",
            linger_ms=5,
        )

    async def start(self):
        await self.producer.start()

    async def stop(self):
        await self.producer.stop()

    async def publish(self, topic: str, event: dict):
        await self.producer.send_and_wait(
            topic,
            json.dumps(event).encode("utf-8"),
        )
