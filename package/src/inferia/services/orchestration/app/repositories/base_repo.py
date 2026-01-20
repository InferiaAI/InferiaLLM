from contextlib import asynccontextmanager


class BaseRepository:
    def __init__(self, db):
        self.db = db

    @asynccontextmanager
    async def transaction(self):
        async with self.db.acquire() as conn:
            async with conn.transaction():
                yield conn
